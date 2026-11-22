"""模块③：残值估值引擎 / Module 3: Residual Value Engine.

生成逐月整机残值曲线：按出厂机龄锚定整机价值，扣除锚点发动机基准
价值得到机体曲线，再逐月加回情景发动机价值，输出三情景到 CSV。

核心逻辑（MODEL_SPEC §3 模块③及校验 #4 修订）：
1. 当前出厂机龄点锚定当前 CMV，15/20/25/30 年点锚定原值配置比例
2. 各锚点整机价值减去对应发动机基准价值，形成机体锚点并对数线性插值
3. 逐月加回发动机调整值 × count，保留 LLP 更换引起的阶梯跳变
4. 情景因子 = 1 + demand_pct；engine_premium_switch 默认关闭

数据来源与可信度：见 MODEL_SPEC §2 校准来源表。公开数据仅支持区间级校准。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from config_loader import REPO_ROOT, SuiteConfig, load_config

# 发动机超额溢价倍数（CFM56-7B 因 LEAP 供应链问题，~$6M → ~$13M）
# 来源：SimpleFlying/avi-go 2026 行情报道；默认关闭，仅作情景分析（MODEL_SPEC §6.3）
ENGINE_PREMIUM_MULTIPLIER = 2.0


def airframe_value(airframe_value_today: float, years_from_now: np.ndarray,
                   anchors: list[tuple[float, float]]) -> np.ndarray:
    """机体价值：以 today 价值为基准，按锚点形状的相对衰减率外推。

    方法：计算锚点曲线的对数斜率（相邻锚点间的年化衰减率），
    将其应用于当前价值，保持曲线形状一致。
    """
    yrs = np.array([a[0] for a in anchors], dtype=float)
    fr = np.array([a[1] for a in anchors], dtype=float)
    # 相邻锚点年化衰减率 r：fr[i+1] = fr[i] * (1 - r)^Δt  →  r = 1 - (fr[i+1]/fr[i])^(1/Δt)
    rates = 1.0 - (fr[1:] / fr[:-1]) ** (1.0 / (yrs[1:] - yrs[:-1]))
    out = np.empty_like(years_from_now, dtype=float)
    for j, t in enumerate(years_from_now):
        v = airframe_value_today
        # 使用锚点加权平均衰减率（按区间长度加权），供独立机体曲线测试使用
        w = (yrs[1:] - yrs[:-1])
        r_eff = float(np.sum(rates * w) / np.sum(w))
        out[j] = v * (1.0 - r_eff) ** t
    return out


def llp_status_factor(cycles: float, llps, shipset_cost: float,
                      replacement_threshold: float) -> float:
    """计算单台发动机维护状态因子（0–1 以上）。

    因子 = 1 − Σ_share × 更换准备计提比例
    某组 LLP 剩余寿命 <= replacement_threshold → 该组从 0 跳变为全额计提，
    保证跨阈值跳变量等于该组 share × 计提强度。
    """
    accrual = 0.0
    for g in llps:
        remaining = g.limit_cycles - cycles
        if remaining <= replacement_threshold:
            accrual += g.shipset_share  # 硬阶梯：阈值以上不计提，阈值处全额计提
    return 1.0 - accrual * min(1.0, shipset_cost / 3.9e6)


def engine_value_single(cycles: float, cfg_engines, scenario_demand_pct: float,
                        premium_switch: bool) -> float:
    """单台发动机价值（美元）。

    基准 = half_life_value 向上/下调整：当前循环相对"半寿"位置，
    半寿定义 = 首修循环数（14,000 FC，Aircraft Commerce）。
    超过 LLP 限额后不再继续恶化，最低保留 half_life_value 的 12% 作为
    part-out 残值；10% 口径下第 25 年发动机占比仅 36.31%，低于校验下限 40%。
    """
    half_life_cycles = cfg_engines.first_shop_visit_cycles
    base = cfg_engines.half_life_value_each_usd
    drift = (cycles - half_life_cycles) / half_life_cycles
    adj = 1.0 - 0.3 * drift  # 半寿两侧近似线性，斜率 0.3（校准参数）
    factor = llp_status_factor(cycles, cfg_engines.llps,
                               cfg_engines.llp_shipset_cost_usd,
                               cfg_engines.llp_replacement_threshold_cycles)
    v = base * adj * factor
    v *= (1.0 + scenario_demand_pct)
    if premium_switch:
        v *= ENGINE_PREMIUM_MULTIPLIER
    return max(v, base * 0.12)


def _log_linear_curve(ages: np.ndarray, anchor_ages: list[float],
                      anchor_values: list[float]) -> np.ndarray:
    """在正值锚点之间进行对数线性插值，末端按最后一段斜率外推。"""
    x = np.asarray(anchor_ages, dtype=float)
    log_y = np.log(np.asarray(anchor_values, dtype=float))
    result = np.empty(len(ages), dtype=float)
    for j, age in enumerate(ages):
        if age <= x[0]:
            i = 0
        elif age >= x[-1]:
            i = len(x) - 2
        else:
            i = int(np.searchsorted(x, age, side="right") - 1)
        slope = (log_y[i + 1] - log_y[i]) / (x[i + 1] - x[i])
        result[j] = float(np.exp(log_y[i] + slope * (age - x[i])))
    return result


def residual_curve(cfg: SuiteConfig, horizon_months: int = 180) -> pd.DataFrame:
    """生成逐月整机残值曲线（index=month，列=各情景，单位美元）。

    月份 0 对应当前资产；当前出厂机龄由当年与出厂年之差确定。
    出厂 15/20/25 年分别对应配置锚点，而非从当前时点再过 15/20/25 年。
    """
    months = np.arange(0, horizon_months + 1)
    current_age = float(date.today().year - cfg.asset.year_of_manufacture)
    ages = current_age + months / 12.0
    fc_per_month = cfg.asset.monthly_utilization_fc

    # 当前 CMV 与配置整机锚点共同构成出厂机龄曲线
    anchor_points = [(current_age, cfg.asset.current_market_value_usd)]
    anchor_points.extend(
        (age, fraction * cfg.asset.purchase_price_usd)
        for age, fraction in cfg.residual_value_curve.anchors()
        if age > current_age
    )
    if len(anchor_points) < 2:
        raise ValueError("当前机龄超出残值锚点范围，无法构造残值曲线")

    # 锚点整机价值扣除对应 base 发动机价值，避免发动机被重复计算
    anchor_ages = [point[0] for point in anchor_points]
    airframe_anchors: list[float] = []
    for age, whole_value in anchor_points:
        cycles = cfg.engines.current_cycles + (age - current_age) * 12.0 * fc_per_month
        engine_at_anchor = engine_value_single(
            cycles, cfg.engines, scenario_demand_pct=0.0, premium_switch=False
        )
        airframe_anchors.append(
            max(whole_value - engine_at_anchor * cfg.engines.count, np.finfo(float).tiny)
        )
    airframe = _log_linear_curve(ages, anchor_ages, airframe_anchors)

    data: dict[str, np.ndarray] = {}
    for name, scenario in cfg.scenarios.items():
        values = np.empty(len(months))
        for j, month in enumerate(months):
            cycles = cfg.engines.current_cycles + month * fc_per_month
            engine = engine_value_single(
                cycles, cfg.engines, scenario.demand_pct,
                cfg.engines.engine_premium_switch
            )
            values[j] = airframe[j] + engine * cfg.engines.count
        data[name] = values

    df = pd.DataFrame(data, index=months)
    df.index.name = "month"
    return df


def main() -> None:
    cfg = load_config()
    df = residual_curve(cfg)
    out_dir = REPO_ROOT / cfg.output.dir
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "residual_curve.csv"
    df.to_csv(out_path)
    print(f"已生成残值曲线 / Residual curve written: {out_path}")
    print(df.tail(3).round(0).to_string())


if __name__ == "__main__":
    main()
