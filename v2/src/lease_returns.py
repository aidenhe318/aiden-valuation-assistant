"""模块①：租赁收益测算 / Module 1: Lease Returns.

按月计算租金、债务摊还、CFADS、DSCR 与权益现金流，并输出三情景
Equity IRR、MOIC、Min DSCR 对比表、IRR 解释及 Excel 可视化仪表盘。

计算口径（MODEL_SPEC §3 模块①）：
1. 月租金 = LRF × 定价基数，按 escalation_pct 逐月复利递增
2. CFADS = 租金 − 运营支出；margin_comp 作为租金成本率冲击
3. 月度 XIRR 先求月内部收益率，再按 (1 + r)^12 − 1 年化
4. 低信用分数触发 LTV 上限及 LRF 加点
5. Equity IRR 10%–20% 为参考区间，超出时输出估值解释而非判定失败
"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence

import numpy as np
import numpy_financial as npf
import pandas as pd
from openpyxl.chart import BarChart, Reference
from openpyxl.styles import Alignment, Font, PatternFill

from config_loader import REPO_ROOT, SuiteConfig, load_config
from engine_residual import residual_curve

# sculpted 摊还的目标偿债覆盖率；对应 MODEL_SPEC §4 校验基准 #2
DEFAULT_TARGET_DSCR = 1.15
# 737-800 月租金合理区间（美元），见 MODEL_SPEC §3 模块①
defaults_B737_800_RENT_RANGE_USD = (150_000.0, 280_000.0)
# Equity IRR 仅作周期参考；15% 以上即解释估值驱动因素
EQUITY_IRR_REFERENCE_RANGE = (0.10, 0.20)
IRR_EXPLANATION_TRIGGER = 0.15


def adjusted_lease_debt_terms(cfg: SuiteConfig) -> tuple[float, float]:
    """返回信用调整后的（LRF, LTV）。

    承租方评分低于 60 时，LTV 不得超过配置上限，LRF 增加配置基点数。
    """
    lrf = cfg.lease.lease_rate_factor
    ltv = cfg.debt.ltv
    credit = cfg.lessee_credit
    if credit.score < 60.0:
        ltv = min(ltv, credit.ltv_cap_low_score)
        lrf += credit.lrf_spread_low_score_bp / 10_000.0
    return lrf, ltv


def monthly_rent_schedule(cfg: SuiteConfig) -> pd.Series:
    """生成逐月租金序列，index 为 1 至租期末月，单位美元。"""
    lrf, _ = adjusted_lease_debt_terms(cfg)
    base_rent = lrf * cfg.lease.pricing_base_usd
    months = np.arange(1, cfg.lease.term_months + 1)
    rents = base_rent * (1.0 + cfg.lease.escalation_pct) ** (months - 1)
    return pd.Series(rents, index=months, name="rent", dtype=float)


def check_rent_reasonableness(cfg: SuiteConfig, monthly_rent_usd: float) -> bool:
    """校验 737-800 月租金区间，偏离时打印联动校验警告。"""
    asset_type = cfg.asset.type.upper().replace("_", "-").replace(" ", "")
    if "737-800" not in asset_type:
        return True
    lower, upper = B737_800_RENT_RANGE_USD
    reasonable = lower <= monthly_rent_usd <= upper
    if not reasonable:
        print(
            f"警告：{cfg.asset.type} 测算月租金 ${monthly_rent_usd:,.0f}/月 "
            f"偏离合理区间 ${lower:,.0f}–${upper:,.0f}/月；"
            "请检查 LRF 与资产定价基数的联动假设。"
        )
    return reasonable


def build_debt_schedule(
    principal: float,
    interest_rate_annual: float,
    term_months: int,
    amortization: str,
    cfads: pd.Series | None = None,
    target_dscr: float = DEFAULT_TARGET_DSCR,
) -> pd.DataFrame:
    """生成月度债务摊还表，支持 monthly_12yr、bullet、sculpted。

    monthly_12yr 按 144 个月等额本息计算固定月供；短于 144 个月的租约
    在期末保留未偿本金。bullet 仅在末期还本。sculpted 按
    CFADS / target_dscr 确定目标还本付息，实际支付不超过当期本息余额。
    """
    if principal < 0.0:
        raise ValueError("债务本金不能为负")
    if term_months <= 0:
        raise ValueError("债务期限必须为正")
    if interest_rate_annual < 0.0:
        raise ValueError("债务年利率不能为负")
    if amortization not in {"monthly_12yr", "bullet", "sculpted"}:
        raise ValueError(f"不支持的摊还方式：{amortization}")
    if amortization == "sculpted":
        if cfads is None or len(cfads) != term_months:
            raise ValueError("sculpted 摊还要求提供与租期等长的 CFADS")
        if target_dscr <= 0.0:
            raise ValueError("目标 DSCR 必须为正")

    monthly_rate = interest_rate_annual / 12.0
    annuity_months = 144
    if monthly_rate == 0.0:
        annuity_payment = principal / annuity_months
    else:
        annuity_payment = principal * monthly_rate / (
            1.0 - (1.0 + monthly_rate) ** -annuity_months
        )

    balance = principal
    rows: list[dict[str, float | int]] = []
    for month in range(1, term_months + 1):
        beginning_balance = balance
        interest = beginning_balance * monthly_rate
        principal_payment = 0.0

        if beginning_balance > 0.0:
            if amortization == "monthly_12yr" and month <= 144:
                principal_payment = min(
                    beginning_balance, max(annuity_payment - interest, 0.0)
                )
            elif amortization == "bullet" and month == term_months:
                principal_payment = beginning_balance
            elif amortization == "sculpted":
                target_service = max(float(cfads.iloc[month - 1]), 0.0) / target_dscr
                principal_payment = min(
                    beginning_balance, max(target_service - interest, 0.0)
                )

        balance = max(beginning_balance - principal_payment, 0.0)
        debt_service = interest + principal_payment
        rows.append(
            {
                "month": month,
                "beginning_balance": beginning_balance,
                "interest_payment": interest,
                "principal_payment": principal_payment,
                "debt_service": debt_service,
                "ending_balance": balance,
            }
        )

    return pd.DataFrame(rows).set_index("month")


def monthly_xirr(cash_flows: Sequence[float], periods_per_year: int = 12) -> float:
    """由等间隔月度现金流计算年化 XIRR。

    先以月度净现值为零求月收益率，再复利年化；现金流必须同时含正负值。
    """
    values = np.asarray(cash_flows, dtype=float)
    if values.ndim != 1 or len(values) < 2:
        raise ValueError("XIRR 至少需要两期现金流")
    if not np.isfinite(values).all():
        raise ValueError("XIRR 现金流必须为有限数值")
    if not ((values > 0.0).any() and (values < 0.0).any()):
        raise ValueError("XIRR 现金流必须同时包含正负值")
    if periods_per_year <= 0:
        raise ValueError("每年期数必须为正")

    monthly_rate = float(npf.irr(values))
    if not np.isfinite(monthly_rate) or monthly_rate <= -1.0:
        raise ValueError("XIRR 未找到有效收益率")
    return (1.0 + monthly_rate) ** periods_per_year - 1.0


def calculate_returns(
    cfg: SuiteConfig, residuals: pd.DataFrame | None = None
) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    """计算各情景收益指标及逐月明细。

    期初权益投入 = 定价基数 − 债务提款；期末权益现金流加回对应情景残值。
    MOIC = 正权益现金流合计 / 权益投入绝对值合计。
    """
    if residuals is None:
        residuals = residual_curve(cfg, horizon_months=cfg.lease.term_months)

    _, effective_ltv = adjusted_lease_debt_terms(cfg)
    debt_principal = cfg.lease.pricing_base_usd * effective_ltv
    initial_equity = cfg.lease.pricing_base_usd - debt_principal
    rents = monthly_rent_schedule(cfg)

    summaries: list[dict[str, float | str]] = []
    monthly_results: dict[str, pd.DataFrame] = {}
    for scenario_name, scenario in cfg.scenarios.items():
        if scenario_name not in residuals.columns:
            raise ValueError(f"残值曲线缺少情景列：{scenario_name}")
        if cfg.lease.term_months not in residuals.index:
            raise ValueError(f"残值曲线缺少期末月份：{cfg.lease.term_months}")

        operating_expense = rents * scenario.margin_comp
        cfads = rents - operating_expense
        debt = build_debt_schedule(
            principal=debt_principal,
            interest_rate_annual=cfg.debt.interest_rate_annual,
            term_months=cfg.lease.term_months,
            amortization=cfg.debt.amortization,
            cfads=cfads,
        )
        debt_service = debt["debt_service"]
        dscr = cfads / debt_service.replace(0.0, np.nan)
        terminal_residual = float(residuals.loc[cfg.lease.term_months, scenario_name])
        equity_cash_flow = cfads - debt_service
        equity_cash_flow.iloc[-1] += terminal_residual
        all_cash_flows = np.concatenate(([-initial_equity], equity_cash_flow.to_numpy()))

        equity_irr = monthly_xirr(all_cash_flows)
        total_recovery = float(all_cash_flows[all_cash_flows > 0.0].sum())
        total_invested = float(-all_cash_flows[all_cash_flows < 0.0].sum())
        moic = total_recovery / total_invested if total_invested > 0.0 else np.nan
        min_dscr = float(dscr.min(skipna=True))

        monthly = pd.concat(
            [
                rents.rename("rent"),
                operating_expense.rename("operating_expense"),
                cfads.rename("cfads"),
                debt,
                dscr.rename("dscr"),
                equity_cash_flow.rename("equity_cash_flow"),
            ],
            axis=1,
        )
        monthly["terminal_residual"] = 0.0
        monthly.iloc[-1, monthly.columns.get_loc("terminal_residual")] = terminal_residual
        monthly_results[scenario_name] = monthly
        summaries.append(
            {
                "Scenario": scenario_name,
                "Equity IRR": equity_irr,
                "MOIC": moic,
                "Min DSCR": min_dscr,
                "Initial Equity": initial_equity,
                "Debt Principal": debt_principal,
                "Terminal Residual": terminal_residual,
                "Ending Debt Balance": float(debt.iloc[-1]["ending_balance"]),
            }
        )

    summary = pd.DataFrame(summaries).set_index("Scenario")
    return summary, monthly_results


def build_irr_explanation(
    cfg: SuiteConfig, equity_irr: float
) -> dict[str, float | str] | None:
    """在 IRR 高于 15% 或低于参考区间时生成估值联动解释。

    局部导入避免 asset_valuation 与 lease_returns 的模块级循环依赖。
    参考区间仅用于风险提示，不作为回滚或调参依据。
    """
    lower, upper = EQUITY_IRR_REFERENCE_RANGE
    if lower <= equity_irr <= IRR_EXPLANATION_TRIGGER:
        return None

    from asset_valuation import calculate_valuation

    valuation, _ = calculate_valuation(cfg)
    row = valuation.iloc[0]
    if equity_irr > upper:
        status = "Above reference range"
    elif equity_irr < lower:
        status = "Below reference range"
    else:
        status = "Within reference range"
    spread_bp = (row["Implied Discount Rate"] - row["WACC"]) * 10_000.0
    explanation = (
        f"Equity IRR {equity_irr:.2%}；10%–20% 为参考区间，超出区间不视为失败。"
        f"整机 DCF 相对 CMV 偏离 {row['Deviation vs CMV']:+.2%}，"
        f"隐含折现率 {row['Implied Discount Rate']:.2%}，WACC {row['WACC']:.2%}"
        f"（差值 {spread_bp:+.1f}bp）。737-800 近期残值坚挺及发动机溢价行情"
        "会随市场周期推高或压低目标 IRR，不应通过调参将结果凑回区间。"
    )
    return {
        "Equity IRR": equity_irr,
        "Reference Range": "10%–20%",
        "Reference Status": status,
        "Deviation vs CMV": float(row["Deviation vs CMV"]),
        "Implied Discount Rate": float(row["Implied Discount Rate"]),
        "WACC": float(row["WACC"]),
        "Implied vs WACC bp": spread_bp,
        "Explanation": explanation,
    }


def _write_dashboard(
    writer: pd.ExcelWriter,
    summary: pd.DataFrame,
    cfg: SuiteConfig,
    irr_explanation: dict[str, float | str] | None,
) -> None:
    """在 Excel 中写入情景图表、租金警告和 IRR 解释汇总。"""
    dashboard = writer.book.create_sheet("Dashboard", 0)
    dashboard.sheet_view.showGridLines = False
    dashboard.merge_cells("A1:H1")
    dashboard["A1"] = "租赁收益与风险仪表盘"
    dashboard["A1"].font = Font(size=18, bold=True, color="FFFFFF")
    dashboard["A1"].fill = PatternFill("solid", fgColor="17365D")
    dashboard["A1"].alignment = Alignment(horizontal="center")
    dashboard.row_dimensions[1].height = 30

    headers = ["Scenario", "Equity IRR", "MOIC", "Min DSCR"]
    for column, header in enumerate(headers, start=1):
        cell = dashboard.cell(row=3, column=column, value=header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="1F4E78")
    for row_number, (scenario, row) in enumerate(summary.iterrows(), start=4):
        dashboard.cell(row=row_number, column=1, value=scenario)
        dashboard.cell(row=row_number, column=2, value=float(row["Equity IRR"]))
        dashboard.cell(row=row_number, column=3, value=float(row["MOIC"]))
        dashboard.cell(row=row_number, column=4, value=float(row["Min DSCR"]))
        dashboard.cell(row=row_number, column=2).number_format = "0.00%"
        dashboard.cell(row=row_number, column=3).number_format = "0.00x"
        dashboard.cell(row=row_number, column=4).number_format = "0.00x"

    irr_chart = BarChart()
    irr_chart.title = "Equity IRR（参考区间 10%–20%）"
    irr_chart.y_axis.title = "年化 IRR"
    irr_chart.y_axis.numFmt = "0%"
    irr_chart.add_data(Reference(dashboard, min_col=2, min_row=3, max_row=6), titles_from_data=True)
    irr_chart.set_categories(Reference(dashboard, min_col=1, min_row=4, max_row=6))
    irr_chart.height = 7
    irr_chart.width = 12
    dashboard.add_chart(irr_chart, "F3")

    dscr_chart = BarChart()
    dscr_chart.title = "Min DSCR（目标 ≥ 1.15x）"
    dscr_chart.y_axis.title = "Min DSCR"
    dscr_chart.add_data(Reference(dashboard, min_col=4, min_row=3, max_row=6), titles_from_data=True)
    dscr_chart.set_categories(Reference(dashboard, min_col=1, min_row=4, max_row=6))
    dscr_chart.height = 7
    dscr_chart.width = 12
    dashboard.add_chart(dscr_chart, "F18")

    dashboard["A8"] = "Equity IRR 参考区间"
    dashboard["B8"] = "10%–20%（周期参考，不作为硬门槛）"
    dashboard["A10"] = "租金合理性校验"
    dashboard["A10"].font = Font(bold=True, color="FFFFFF")
    dashboard["A10"].fill = PatternFill("solid", fgColor="1F4E78")
    lrf, _ = adjusted_lease_debt_terms(cfg)
    monthly_rent = lrf * cfg.lease.pricing_base_usd
    lower, upper = B737_800_RENT_RANGE_USD
    rent_reasonable = lower <= monthly_rent <= upper
    dashboard["A11"] = "测算月租金（美元）"
    dashboard["B11"] = monthly_rent
    dashboard["B11"].number_format = "$#,##0"
    dashboard["A12"] = "合理区间（美元/月）"
    dashboard["B12"] = f"${lower:,.0f}–${upper:,.0f}"
    dashboard["A13"] = "状态"
    dashboard["B13"] = (
        "通过：月租金处于合理区间"
        if rent_reasonable
        else "警告：月租金偏离合理区间，请检查 LRF 与资产定价基数联动"
    )
    dashboard["B13"].fill = PatternFill(
        "solid", fgColor="C6EFCE" if rent_reasonable else "FFC7CE"
    )

    dashboard["A15"] = "IRR 参考区间解释"
    dashboard["A15"].font = Font(bold=True, color="FFFFFF")
    dashboard["A15"].fill = PatternFill("solid", fgColor="1F4E78")
    dashboard.merge_cells("A16:D18")
    dashboard["A16"] = (
        irr_explanation["Explanation"]
        if irr_explanation is not None
        else "base Equity IRR 未触发解释阈值；参考区间仅用于周期分析。"
    )
    dashboard["A16"].alignment = Alignment(wrap_text=True, vertical="top")
    for column in ("A", "B", "C", "D"):
        dashboard.column_dimensions[column].width = 28
    dashboard.column_dimensions["E"].width = 3


def write_returns_report(
    summary: pd.DataFrame,
    monthly_results: dict[str, pd.DataFrame],
    output_path: str | Path,
    cfg: SuiteConfig | None = None,
    irr_explanation: dict[str, float | str] | None = None,
) -> Path:
    """将三情景汇总、逐月明细、诊断与可视化仪表盘写入 Excel。"""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        summary.to_excel(writer, sheet_name="Summary")
        for scenario_name, monthly in monthly_results.items():
            monthly.to_excel(writer, sheet_name=scenario_name[:31])
        if irr_explanation is not None:
            pd.DataFrame([irr_explanation]).to_excel(
                writer, sheet_name="Diagnostics", index=False
            )
        if cfg is not None:
            _write_dashboard(writer, summary, cfg, irr_explanation)
    return path


def main() -> None:
    """加载统一配置，运行三情景测算并生成 returns_report.xlsx。"""
    cfg = load_config()
    lrf, _ = adjusted_lease_debt_terms(cfg)
    check_rent_reasonableness(cfg, lrf * cfg.lease.pricing_base_usd)
    summary, monthly_results = calculate_returns(cfg)
    irr_explanation = build_irr_explanation(cfg, summary.loc["base", "Equity IRR"])
    output_path = REPO_ROOT / cfg.output.dir / "returns_report.xlsx"
    write_returns_report(summary, monthly_results, output_path, cfg, irr_explanation)

    display = summary[["Equity IRR", "MOIC", "Min DSCR"]].copy()
    print("三情景租赁收益对比 / Lease returns by scenario")
    print(
        display.to_string(
            formatters={
                "Equity IRR": lambda x: f"{x:.2%}",
                "MOIC": lambda x: f"{x:.2f}x",
                "Min DSCR": lambda x: f"{x:.2f}x",
            }
        )
    )
    if irr_explanation is not None:
        print("\nIRR 参考区间解释 / IRR diagnostics")
        print(irr_explanation["Explanation"])
    print(f"已生成租赁收益报告 / Returns report written: {output_path}")


if __name__ == "__main__":
    main()
