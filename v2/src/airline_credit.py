"""task-06：五家航司信用评分接入 / Airline credit integration.

读取参考项目 A 的标准化分项数据，按 40%/30%/20%/10% 权重复算
Ryanair、AirAsiaX、Delta、easyJet、Lufthansa 的信用分，并接入
lease_returns 现有的低分 LTV 上限与 LRF 加点规则。

数据来自参考 notebook 对已公开财报的比率计算结果；本模块不重新下载财报，
也不把锚点分数直接当作模型计算结果。基准分必须与参考输出逐项一致。
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill

from config_loader import REPO_ROOT, SuiteConfig, load_config
from lease_returns import adjusted_lease_debt_terms

DEFAULT_INPUT_PATH = REPO_ROOT / "data" / "airline_credit_inputs.csv"
SOURCE_URL = (
    "https://github.com/antoinejrnc/asset-based-finance-models/"
    "blob/main/Airline_Default_Risk_Model.ipynb"
)
WEIGHTS = {
    "liquidity_raw": 0.40,
    "leverage_raw": 0.30,
    "profitability_raw": 0.20,
    "efficiency_cash_raw": 0.10,
}


def load_airline_credit_inputs(path: str | Path | None = None) -> pd.DataFrame:
    """读取并校验五家航司的标准化信用分项数据。"""
    input_path = Path(path) if path is not None else DEFAULT_INPUT_PATH
    data = pd.read_csv(input_path)
    required = {
        "airline",
        "ticker",
        "liquidity_raw",
        "leverage_raw",
        "profitability_raw",
        "efficiency_cash_raw",
        "reference_base_score",
        "reference_downside_score",
        "reference_severe_score",
    }
    missing = required.difference(data.columns)
    if missing:
        raise ValueError(f"航司信用输入缺少字段：{sorted(missing)}")
    if data["airline"].duplicated().any():
        raise ValueError("航司名称不能重复")

    numeric_columns = list(required.difference({"airline", "ticker"}))
    if not np.isfinite(data[numeric_columns].to_numpy(dtype=float)).all():
        raise ValueError("航司信用分项与参考分数必须为有限数值")
    return data


def _score_to_risk(score: float) -> str:
    """沿用参考项目的五档风险分层。"""
    if score >= 75.0:
        return "Low"
    if score >= 60.0:
        return "Moderate"
    if score >= 45.0:
        return "Elevated"
    if score >= 30.0:
        return "High"
    return "Very High"


def build_credit_comparison(
    cfg: SuiteConfig, inputs: pd.DataFrame | None = None
) -> pd.DataFrame:
    """复算信用分并映射为各航司的有效 LRF、LTV 与月租金。"""
    data = load_airline_credit_inputs() if inputs is None else inputs.copy()
    data["Credit Score"] = sum(
        data[column] * weight for column, weight in WEIGHTS.items()
    )
    if not np.allclose(
        data["Credit Score"], data["reference_base_score"], atol=1e-9, rtol=0.0
    ):
        raise ValueError("复算信用分与参考锚点不一致，请检查分项数据或权重")

    rows: list[dict[str, float | str | bool]] = []
    for _, source in data.iterrows():
        score = float(source["Credit Score"])
        airline_cfg = replace(
            cfg,
            lessee_credit=replace(cfg.lessee_credit, score=score),
        )
        effective_lrf, effective_ltv = adjusted_lease_debt_terms(airline_cfg)
        rows.append(
            {
                "Airline": source["airline"],
                "Ticker": source["ticker"],
                "Liquidity Raw": float(source["liquidity_raw"]),
                "Leverage Raw": float(source["leverage_raw"]),
                "Profitability Raw": float(source["profitability_raw"]),
                "Efficiency Cash Raw": float(source["efficiency_cash_raw"]),
                "Credit Score": score,
                "Reference Base Score": float(source["reference_base_score"]),
                "Downside Score": float(source["reference_downside_score"]),
                "Severe Score": float(source["reference_severe_score"]),
                "Risk Band": _score_to_risk(score),
                "Credit Adjustment Triggered": score < 60.0,
                "Effective LRF": effective_lrf,
                "Effective LTV": effective_ltv,
                "Monthly Rent USD": effective_lrf * cfg.lease.pricing_base_usd,
            }
        )
    return pd.DataFrame(rows)


def write_credit_report(comparison: pd.DataFrame, output_path: str | Path) -> Path:
    """输出航司信用分明细及权重、来源和租赁调整规则。"""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    methodology = pd.DataFrame(
        [
            ("评分权重", "流动性 / 杠杆 / 盈利能力 / 效率与现金 = 40% / 30% / 20% / 10%"),
            ("数据来源", SOURCE_URL),
            ("数据口径", "参考 notebook 基于公开财报计算的标准化分项；本模块复算加权分并校验锚点"),
            ("信用调整", "score < 60 时应用 config 的 LTV 上限与 LRF 加点；其他航司沿用基础条款"),
            ("风险分层", "Low ≥75；Moderate ≥60；Elevated ≥45；High ≥30；其余 Very High"),
            ("名称校验", "Lufthansa 为正确拼写；AirAsiaX 对应 ticker 5238.KL"),
        ],
        columns=["项目", "说明"],
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        comparison.to_excel(writer, sheet_name="Credit Scores", index=False)
        methodology.to_excel(writer, sheet_name="Methodology", index=False)
        for worksheet in writer.book.worksheets:
            worksheet.freeze_panes = "A2"
            worksheet.auto_filter.ref = worksheet.dimensions
            worksheet.sheet_view.showGridLines = False
            for cell in worksheet[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="1F4E78")
            for column in worksheet.columns:
                letter = column[0].column_letter
                width = max(len(str(cell.value or "")) for cell in column) + 2
                worksheet.column_dimensions[letter].width = min(max(width, 12), 80)
        for row in writer.book["Methodology"].iter_rows(min_row=2):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
    return path


def main() -> None:
    """运行五家航司信用评分接入示例并生成 Excel 报告。"""
    cfg = load_config()
    comparison = build_credit_comparison(cfg)
    output_path = REPO_ROOT / cfg.output.dir / "airline_credit_report.xlsx"
    write_credit_report(comparison, output_path)
    display = comparison[
        ["Airline", "Credit Score", "Risk Band", "Effective LTV", "Effective LRF"]
    ].copy()
    print(
        display.to_string(
            index=False,
            formatters={
                "Credit Score": lambda value: f"{value:.2f}",
                "Effective LTV": lambda value: f"{value:.0%}",
                "Effective LRF": lambda value: f"{value:.2%}",
            },
        )
    )
    print(f"已生成航司信用报告 / Airline credit report written: {output_path}")


if __name__ == "__main__":
    main()
