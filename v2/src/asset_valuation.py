"""模块②：资产估值 / Module 2: Asset Valuation.

以统一配置为基础，先计算整机无杠杆 DCF，再按当前机体与发动机的
相对价值分摊，并单独列示发动机维护状态调整，输出整机估值、
隐含折现率、敏感性矩阵及 Excel 报告。

计算口径（MODEL_SPEC §3 模块②及 task-04 修订）：
1. 整机现金流采用 base 情景月租金，期末加回 base 整机残值
2. 按当前机体价值与 engine_value_single 价值的相对比例分摊整体 DCF
3. 单台发动机维护调整 = 当前发动机价值 − 半寿基准价值
4. 年 WACC 转换为有效月利率，现金流按月末复利折现
5. 整机公允价值严格等于机体 DCF 与全部发动机 DCF 之和
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

from config_loader import REPO_ROOT, SuiteConfig, load_config
from engine_residual import engine_value_single, residual_curve
from lease_returns import monthly_rent_schedule


@dataclass
class _ValuationInputs:
    """估值中不随折现率变化的现金流与当前价值输入。"""

    whole_cash_flow: pd.Series
    current_airframe_value: float
    current_engine_value_each: float
    maintenance_adjustment_each: float


def discount_cash_flows(
    cash_flows: Sequence[float] | pd.Series, annual_wacc: float
) -> float:
    """将月末现金流按有效月利率折现并求和，单位与输入一致。"""
    values = np.asarray(cash_flows, dtype=float)
    if values.ndim != 1 or len(values) == 0:
        raise ValueError("DCF 至少需要一期现金流")
    if not np.isfinite(values).all():
        raise ValueError("DCF 现金流必须为有限数值")
    if annual_wacc <= -1.0:
        raise ValueError("年折现率必须大于 -100%")

    monthly_rate = (1.0 + annual_wacc) ** (1.0 / 12.0) - 1.0
    periods = np.arange(1, len(values) + 1, dtype=float)
    return float(np.sum(values / (1.0 + monthly_rate) ** periods))


def _valuation_inputs(cfg: SuiteConfig) -> _ValuationInputs:
    """构造整机现金流、当前相对价值及当前维护状态调整。"""
    rents = monthly_rent_schedule(cfg)
    residuals = residual_curve(cfg, horizon_months=cfg.lease.term_months)
    term_month = cfg.lease.term_months
    if term_month not in residuals.index or "base" not in residuals.columns:
        raise ValueError("残值曲线缺少 base 情景或租约期末月份")

    whole_cash_flow = rents.copy()
    whole_cash_flow.iloc[-1] += float(residuals.loc[term_month, "base"])
    current_engine_each = engine_value_single(
        cfg.engines.current_cycles,
        cfg.engines,
        scenario_demand_pct=cfg.scenarios["base"].demand_pct,
        premium_switch=cfg.engines.engine_premium_switch,
    )
    current_engines = current_engine_each * cfg.engines.count
    current_airframe = max(cfg.asset.current_market_value_usd - current_engines, 0.0)
    maintenance_adjustment = (
        current_engine_each - cfg.engines.half_life_value_each_usd
    )
    return _ValuationInputs(
        whole_cash_flow=whole_cash_flow,
        current_airframe_value=current_airframe,
        current_engine_value_each=current_engine_each,
        maintenance_adjustment_each=maintenance_adjustment,
    )


def _evaluate_valuation(
    cfg: SuiteConfig, inputs: _ValuationInputs, annual_wacc: float
) -> dict[str, float | bool]:
    """在指定折现率下分摊整体 DCF，并计算维护调整后的部件价值。"""
    whole_dcf = discount_cash_flows(inputs.whole_cash_flow, annual_wacc)
    current_engines = inputs.current_engine_value_each * cfg.engines.count
    current_total = inputs.current_airframe_value + current_engines
    if current_total <= 0.0:
        raise ValueError("当前机体与发动机价值合计必须为正")

    airframe_value = whole_dcf * inputs.current_airframe_value / current_total
    engines_allocated = whole_dcf * current_engines / current_total
    engine_allocated_each = engines_allocated / cfg.engines.count
    engine_values = [
        engine_allocated_each + inputs.maintenance_adjustment_each
        for _ in range(cfg.engines.count)
    ]
    engines_value = float(sum(engine_values))
    maintenance_adjustment = (
        inputs.maintenance_adjustment_each * cfg.engines.count
    )
    aircraft_value = airframe_value + engines_value
    deviation = aircraft_value / cfg.asset.current_market_value_usd - 1.0

    result: dict[str, float | bool] = {
        "Unlevered Aircraft DCF": whole_dcf,
        "Airframe DCF": airframe_value,
        "Engines Allocated DCF": engines_allocated,
        "Maintenance Adjustment": maintenance_adjustment,
        "Engines DCF": engines_value,
        "Aircraft Fair Value": aircraft_value,
        "Current Market Value": cfg.asset.current_market_value_usd,
        "Deviation vs CMV": deviation,
        "CMV Within 25%": abs(deviation) < 0.25,
        "WACC": annual_wacc,
        "LRF": cfg.lease.lease_rate_factor,
        "Current Airframe Value": inputs.current_airframe_value,
        "Current Engine Value Each": inputs.current_engine_value_each,
    }
    for engine_number, value in enumerate(engine_values, start=1):
        result[f"Engine {engine_number} DCF"] = value
    return result


def _implied_rate_from_inputs(
    cfg: SuiteConfig, inputs: _ValuationInputs, target_value: float
) -> float:
    """用二分法求使维护调整后整机 DCF 等于目标价值的年折现率。"""
    if target_value <= 0.0:
        raise ValueError("目标整机价值必须为正")

    def value_at(rate: float) -> float:
        return float(_evaluate_valuation(cfg, inputs, rate)["Aircraft Fair Value"])

    lower, upper = -0.99, 1.0
    if value_at(lower) < target_value:
        raise ValueError("隐含折现率求解下界未能覆盖目标价值")
    while value_at(upper) > target_value:
        upper = upper * 2.0 + 1.0
        if upper > 100.0:
            raise ValueError("隐含折现率求解上界未能覆盖目标价值")

    for _ in range(100):
        midpoint = (lower + upper) / 2.0
        if value_at(midpoint) > target_value:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0


def implied_discount_rate(
    cfg: SuiteConfig, target_value: float | None = None
) -> float:
    """返回使维护调整后整机 DCF 等于当前 CMV 的年化折现率。"""
    target = cfg.asset.current_market_value_usd if target_value is None else target_value
    return _implied_rate_from_inputs(cfg, _valuation_inputs(cfg), target)


def calculate_valuation(
    cfg: SuiteConfig,
    annual_wacc: float | None = None,
    include_implied_rate: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """计算整体 DCF、相对价值分摊、维护调整及逐月整机现金流。"""
    wacc = cfg.wacc.wacc if annual_wacc is None else annual_wacc
    inputs = _valuation_inputs(cfg)
    summary_data = _evaluate_valuation(cfg, inputs, wacc)
    if include_implied_rate:
        summary_data["Implied Discount Rate"] = _implied_rate_from_inputs(
            cfg, inputs, cfg.asset.current_market_value_usd
        )

    monthly = pd.DataFrame(index=inputs.whole_cash_flow.index)
    monthly.index.name = "month"
    monthly["Base Rent"] = monthly_rent_schedule(cfg)
    monthly["Terminal Residual"] = 0.0
    monthly.iloc[-1, monthly.columns.get_loc("Terminal Residual")] = (
        inputs.whole_cash_flow.iloc[-1] - monthly["Base Rent"].iloc[-1]
    )
    monthly["Unlevered Aircraft Cash Flow"] = inputs.whole_cash_flow

    summary = pd.DataFrame([summary_data], index=["Base"])
    summary.index.name = "Scenario"
    return summary, monthly


def sensitivity_analysis(cfg: SuiteConfig) -> pd.DataFrame:
    """输出 WACC ±200bp、LRF ±25bp 的整机公允价值敏感性矩阵。"""
    wacc_cases = [
        ("WACC -200bp", cfg.wacc.wacc - 0.02),
        ("WACC Base", cfg.wacc.wacc),
        ("WACC +200bp", cfg.wacc.wacc + 0.02),
    ]
    lrf_cases = [
        ("LRF -25bp", cfg.lease.lease_rate_factor - 0.0025),
        ("LRF Base", cfg.lease.lease_rate_factor),
        ("LRF +25bp", cfg.lease.lease_rate_factor + 0.0025),
    ]

    matrix = pd.DataFrame(
        index=[name for name, _ in wacc_cases],
        columns=[name for name, _ in lrf_cases],
        dtype=float,
    )
    matrix.index.name = "WACC"
    matrix.columns.name = "LRF"
    for wacc_name, wacc in wacc_cases:
        for lrf_name, lrf in lrf_cases:
            variant = replace(cfg, lease=replace(cfg.lease, lease_rate_factor=lrf))
            summary, _ = calculate_valuation(
                variant, annual_wacc=wacc, include_implied_rate=False
            )
            matrix.loc[wacc_name, lrf_name] = summary.iloc[0]["Aircraft Fair Value"]
    return matrix


def _assumptions_table(cfg: SuiteConfig) -> pd.DataFrame:
    """生成假设与来源表，保留 MODEL_SPEC §2 的八行来源口径。"""
    monthly_rent = cfg.lease.lease_rate_factor * cfg.lease.pricing_base_usd
    rows = [
        (
            "737-800 10 年机龄 CMV $22–26M",
            f"${cfg.asset.current_market_value_usd / 1e6:.1f}M",
            "IBA、Ishka",
            "高",
        ),
        (
            "LRF 0.65–0.75%/月；737-800 月租 $230–250k",
            f"LRF {cfg.lease.lease_rate_factor:.2%}/月；月租 ${monthly_rent / 1e3:.0f}k",
            "IBA 2024-09、Ishka",
            "高",
        ),
        (
            "LTV 65–80%；利率 4–6.5%",
            f"LTV {cfg.debt.ltv:.0%}；利率 {cfg.debt.interest_rate_annual:.1%}",
            "AerCap 20-F、Avation、FLYING Finance",
            "高",
        ),
        (
            "CFM56-7B LLP：Fan/LPC 30k、HPC/HPT 20k、LPT 25k FC",
            "；".join(f"{g.group} {g.limit_cycles / 1e3:.0f}k FC" for g in cfg.engines.llps),
            "Aircraft Commerce、EASA TCDS ALS",
            "高",
        ),
        (
            "LLP shipset ~$3.9M；大修 $1.3M",
            f"LLP ${cfg.engines.llp_shipset_cost_usd / 1e6:.1f}M；"
            f"大修 ${cfg.engines.shop_visit_cost_usd / 1e6:.1f}M",
            "Aircraft Commerce（-5B 外推）",
            "中",
        ),
        (
            "发动机半寿 $4–6M；full-life ~$13M（2026 行情）",
            f"半寿单台 ${cfg.engines.half_life_value_each_usd / 1e6:.1f}M；"
            f"溢价开关 {'开启' if cfg.engines.engine_premium_switch else '关闭'}",
            "IBA、SimpleFlying、avi-go",
            "高/中",
        ),
        (
            "15 年残值 40%；20–25 年 10–35%",
            f"15/20/25 年 = {cfg.residual_value_curve.year_15:.0%}/"
            f"{cfg.residual_value_curve.year_20:.0%}/{cfg.residual_value_curve.year_25:.0%}",
            "mba Aviation 737-800 Market Profile",
            "中",
        ),
        (
            "目标 Equity IRR 10–15%",
            "10–15%（校验目标）",
            "Aircastle/AerCap 公开表述",
            "中",
        ),
        (
            "WACC 与隐含折现率",
            "WACC 由 config 构建；隐含折现率使维护调整后 DCF = 当前 CMV",
            "config：rf + β×ERP、kd×(1−tax)、目标 D/E；二分法求解",
            "模型计算",
        ),
        (
            "整机无杠杆 DCF 与相对价值分摊",
            "租金 + 期末整机残值先整体折现，再按当前机体与发动机相对价值分摊",
            "task-04 修订口径；避免租金重复分摊及维修成本重复扣减",
            "模型假设",
        ),
        (
            "发动机维护状态调整",
            "单台调整 = 当前 engine_value_single − 半寿基准；状态优于半寿时为正",
            "task-04 修订口径；作为独立估值调整项而非未来成本扣除",
            "模型假设",
        ),
        (
            "发动机 part-out 价值下限",
            "half_life_value_each_usd 的 12%",
            "engine_residual.py 现行老龄价值下限口径",
            "模型假设",
        ),
        (
            "期末残值处理",
            f"第 {cfg.lease.term_months} 月 base 整机残值计入整机现金流后再分摊",
            "task-04 修订口径；part-out 下限为半寿价值的 12%",
            "模型假设",
        ),
    ]
    return pd.DataFrame(rows, columns=["参数", "默认值", "来源", "可信度"])


def write_valuation_report(
    summary: pd.DataFrame,
    monthly: pd.DataFrame,
    sensitivity: pd.DataFrame,
    output_path: str | Path,
    cfg: SuiteConfig,
) -> Path:
    """将估值、敏感性、逐月明细及假设来源写入 Excel 报告。"""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Valuation", index=False)
        sensitivity.to_excel(writer, sheet_name="Sensitivity")
        monthly.to_excel(writer, sheet_name="Monthly Detail")
        _assumptions_table(cfg).to_excel(
            writer, sheet_name="Assumptions", index=False
        )

        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            worksheet.sheet_view.showGridLines = False
            for cell in worksheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1F4E78")
                cell.alignment = Alignment(vertical="center")
            for column in worksheet.columns:
                letter = column[0].column_letter
                width = max(len(str(cell.value or "")) for cell in column) + 2
                worksheet.column_dimensions[letter].width = min(max(width, 12), 60)

        assumptions = writer.book["Assumptions"]
        for row in assumptions.iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(vertical="top", wrap_text=True)
        assumptions.column_dimensions["A"].width = 52
        assumptions.column_dimensions["B"].width = 60
        assumptions.column_dimensions["C"].width = 45

        for sheet_name in ("Valuation", "Sensitivity", "Monthly Detail"):
            worksheet = writer.book[sheet_name]
            for row in worksheet.iter_rows(min_row=2):
                for cell in row:
                    if isinstance(cell.value, (int, float)) and not isinstance(
                        cell.value, bool
                    ):
                        cell.number_format = "#,##0.00"

    return path


def main() -> None:
    """运行基准估值、隐含折现率、敏感性分析并生成 Excel 报告。"""
    cfg = load_config()
    summary, monthly = calculate_valuation(cfg)
    sensitivity = sensitivity_analysis(cfg)
    output_path = REPO_ROOT / cfg.output.dir / "valuation_report.xlsx"
    write_valuation_report(summary, monthly, sensitivity, output_path, cfg)

    row = summary.iloc[0]
    print("三部件估值 / Asset valuation (USD)")
    print(f"机体 DCF / Airframe DCF:       ${row['Airframe DCF']:,.0f}")
    print(f"发动机 DCF / Engines DCF:      ${row['Engines DCF']:,.0f}")
    print(f"整机公允价值 / Aircraft Value: ${row['Aircraft Fair Value']:,.0f}")
    print(f"相对 CMV 偏差 / Deviation:     {row['Deviation vs CMV']:.2%}")
    print(f"维护状态调整 / Adjustment:     ${row['Maintenance Adjustment']:,.0f}")
    print(f"WACC:                          {row['WACC']:.4%}")
    print(f"隐含折现率 / Implied Rate:     {row['Implied Discount Rate']:.4%}")
    print(
        "隐含折现率 − WACC:             "
        f"{(row['Implied Discount Rate'] - row['WACC']) * 10_000:.1f}bp"
    )
    print("\n敏感性矩阵 / Sensitivity (USD million)")
    print((sensitivity / 1e6).round(3).to_string())
    print(f"已生成资产估值报告 / Valuation report written: {output_path}")


if __name__ == "__main__":
    main()
