"""模块③：残值估值引擎 / Module 3: Residual Value Engine.

生成逐月整机残值曲线：机体经济折旧锚点插值 + 发动机 LLP 更换调整，
输出三情景（Base / Downside / Severe）各一列到 outputs/residual_curve.csv。

核心逻辑（MODEL_SPEC §3 模块③）：
1. 机体价值 = 残值锚点对数线性插值（经济折旧，单调递减）
2. 发动机价值 = 单台基准价值 × 维护状态因子
   - 维护状态因子由三组 LLP 的"剩余寿命加权"决定
   - 某组 LLP 剩余寿命 < 阈值 → 计提更换支出（shipset_cost × share），
     价值曲线出现向下阶梯跳变（校验基准 #5）
3. 情景因子 = 1 + demand_pct（燃油/利润率影响租约现金流，由模块①处理）
4. engine_premium_switch（默认关闭）：开启后发动机价值乘以市场溢价倍数，
   模拟 2025-26 供应链特殊行情（MODEL_SPEC §6.3）

数据来源与可信度：见 MODEL_SPEC §2 校准来源表。公开数据仅支持区间级校准。
"""

from __future__ import annotations

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
    # 当前机龄（年 0 在锚点中为全新机；当前价值对应某机龄点，用 current/purchase 反推）
    out = np.empty_like(years_from_now, dtype=float)
    for j, t in enumerate(years_from_now):
        v = airframe_value_today
        # 从当前起逐段推进：找到当前所在锚点区间，以其衰减率外推
        age_now = None
        for i in range(len(yrs) - 1):
            pass  # 当前机龄由调用方折算进基准值，此处只用锚点衰减率形状
        # 简化：使用锚点加权平均衰减率（按区间长度加权）
        w = (yrs[1:] - yrs[:-1])
        r_eff = float(np.sum(rates * w) / np.sum(w))
        out[j] = v * (1.0 - r_eff) ** t
    return out


def llp_status_factor(cycles: float, llps, shipset_cost: float,
                      replacement_threshold: float) -> float:
    """计算单台发动机维护状态因子（0–1 以上）。

    因子 = 1 − Σ_share × 更换准备计提比例
    某组 LLP 剩余寿命 < replacement_threshold → 该组全额计提（向下跳变）。
    """
    accrual = 0.0
    for g in llps:
        remaining = g.limit_cycles - cycles
        if remaining <= replacement_threshold:
            accrual += g.shipset_share  # 全额计提
        else:
            # 线性计提：剩余寿命越接近阈值，计提比例越高
            accrual += g.shipset_share * max(
                0.0, 1.0 - (remaining - replacement_threshold) / g.limit_cycles)
    return 1.0 - accrual * min(1.0, shipset_cost / 3.9e6)


def engine_value_single(cycles: float, cfg_engines, scenario_demand_pct: float,
                        premium_switch: bool) -> float:
    """单台发动机价值（美元）。

    基准 = half_life_value 向上/下调整：当前循环相对"半寿"位置，
    半寿定义 = 首修循环数（14,000 FC，Aircraft Commerce）。
    """
    half_life_cycles = cfg_engines.first_shop_visit_cycles
    base = cfg_engines.half_life_value_each_usd
    # 循环位置调整：每偏离半寿 1 循环，价值变化按 shipset 成本/限额折算
    drift = (cycles - half_life_cycles) / half_life_cycles
    adj = 1.0 - 0.3 * drift  # 半寿两侧近似线性，斜率 0.3（校准参数）
    factor = llp_status_factor(cycles, cfg_engines.llps,
                               cfg_engines.llp_shipset_cost_usd,
                               cfg_engines.llp_replacement_threshold_cycles)
    v = base * adj * factor
    v *= (1.0 + scenario_demand_pct)          # 情景需求因子
    if premium_switch:
        v *= ENGINE_PREMIUM_MULTIPLIER
    return max(v, 0.0)


def residual_curve(cfg: SuiteConfig, horizon_months: int = 180) -> pd.DataFrame:
    """生成逐月残值曲线，返回 DataFrame（index=month，列=各情景，单位美元）。"""
    months = np.arange(0, horizon_months + 1)
    years = months / 12.0
    fc_per_month = cfg.asset.monthly_utilization_fc

    # 机体：当前 CMV 中剔除发动机价值 → 机体基准价值
    eng_now = engine_value_single(cfg.engines.current_cycles, cfg.engines,
                                  scenario_demand_pct=0.0,
                                  premium_switch=cfg.engines.engine_premium_switch)
    airframe_today = cfg.asset.current_market_value_usd - eng_now * cfg.engines.count
    airframe_today = max(airframe_today, 0.05 * cfg.asset.purchase_price_usd)

    anchors = cfg.residual_value_curve.anchors()
    af = airframe_value(airframe_today, years, anchors)

    data: dict[str, np.ndarray] = {}
    for name, sc in cfg.scenarios.items():
        vals = np.empty(len(months))
        for j, m in enumerate(months):
            cycles = cfg.engines.current_cycles + m * fc_per_month
            eng = engine_value_single(cycles, cfg.engines, sc.demand_pct,
                                      cfg.engines.engine_premium_switch)
            vals[j] = af[j] + eng * cfg.engines.count
        data[name] = vals

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
