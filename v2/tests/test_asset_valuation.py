"""模块②测试：资产估值 / Tests for asset valuation.

覆盖 MODEL_SPEC 校验基准 #7、#8：
- 月度复利 DCF 与手算样例误差小于 1e-6
- 整机无杠杆 DCF 按当前相对价值分摊，并单独调整维护状态
- 机体、发动机与整机估值满足拆分恒等式且各部件非负
- 隐含折现率使维护调整后 DCF 回到当前 CMV
- WACC 与 LRF 敏感性方向正确，报告包含假设与来源
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from asset_valuation import (  # noqa: E402
    calculate_valuation,
    discount_cash_flows,
    implied_discount_rate,
    sensitivity_analysis,
    write_valuation_report,
)
from config_loader import load_config  # noqa: E402
from engine_residual import engine_value_single  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return load_config()


class TestDiscountCashFlows:
    def test_hand_calculated_three_month_cash_flows(self):
        """三期现金流按有效年利率转换为月利率后逐期折现。"""
        annual_wacc = 0.12
        monthly_rate = (1.0 + annual_wacc) ** (1.0 / 12.0) - 1.0
        expected = (
            1_000.0 / (1.0 + monthly_rate)
            + 1_100.0 / (1.0 + monthly_rate) ** 2
            + 1_200.0 / (1.0 + monthly_rate) ** 3
        )

        result = discount_cash_flows([1_000.0, 1_100.0, 1_200.0], annual_wacc)

        assert result == pytest.approx(expected, abs=1e-6)


class TestValuation:
    def test_aircraft_equals_airframe_plus_engines(self, cfg):
        summary, monthly = calculate_valuation(cfg)
        row = summary.iloc[0]
        component_sum = row["Airframe DCF"] + row["Engines DCF"]

        assert row["Aircraft Fair Value"] == pytest.approx(component_sum, abs=1e-8)
        assert abs(row["Aircraft Fair Value"] / component_sum - 1.0) < 0.01
        assert len(monthly) == cfg.lease.term_months

    def test_components_are_nonnegative_and_within_cmv_calibration(self, cfg):
        summary, _ = calculate_valuation(cfg)
        row = summary.iloc[0]

        assert row["Airframe DCF"] >= 0.0
        assert row["Engines DCF"] >= 0.0
        assert row["Engine 1 DCF"] >= 0.0
        assert row["Engine 2 DCF"] >= 0.0
        assert 18_000_000.0 <= row["Aircraft Fair Value"] <= 30_000_000.0
        assert abs(row["Deviation vs CMV"]) < 0.25

    def test_maintenance_adjustment_uses_current_half_life_gap(self, cfg):
        summary, _ = calculate_valuation(cfg)
        row = summary.iloc[0]
        current_engine = engine_value_single(
            cfg.engines.current_cycles, cfg.engines, 0.0, False
        )
        expected_adjustment = (
            current_engine - cfg.engines.half_life_value_each_usd
        ) * cfg.engines.count

        assert row["Maintenance Adjustment"] == pytest.approx(expected_adjustment)
        assert row["Aircraft Fair Value"] == pytest.approx(
            row["Unlevered Aircraft DCF"] + expected_adjustment
        )
        assert row["Engines Allocated DCF"] > row["Engines DCF"]

    def test_implied_discount_rate_reprices_to_current_market_value(self, cfg):
        implied_rate = implied_discount_rate(cfg)
        summary, _ = calculate_valuation(
            cfg, annual_wacc=implied_rate, include_implied_rate=False
        )

        assert summary.iloc[0]["Aircraft Fair Value"] == pytest.approx(
            cfg.asset.current_market_value_usd, abs=1e-4
        )


class TestSensitivity:
    def test_wacc_and_lrf_move_valuation_in_expected_direction(self, cfg):
        matrix = sensitivity_analysis(cfg)

        assert matrix.shape == (3, 3)
        assert matrix.loc["WACC +200bp", "LRF Base"] < matrix.loc[
            "WACC Base", "LRF Base"
        ]
        assert matrix.loc["WACC -200bp", "LRF Base"] > matrix.loc[
            "WACC Base", "LRF Base"
        ]
        assert matrix.loc["WACC Base", "LRF -25bp"] < matrix.loc[
            "WACC Base", "LRF Base"
        ]
        assert matrix.loc["WACC Base", "LRF +25bp"] > matrix.loc[
            "WACC Base", "LRF Base"
        ]


class TestValuationReport:
    def test_report_contains_valuation_sensitivity_and_sources(self, cfg, tmp_path):
        summary, monthly = calculate_valuation(cfg)
        sensitivity = sensitivity_analysis(cfg)
        output_path = tmp_path / "valuation_report.xlsx"

        write_valuation_report(summary, monthly, sensitivity, output_path, cfg)

        assert output_path.exists()
        sheets = pd.read_excel(output_path, sheet_name=None)
        assert set(sheets) == {
            "Valuation",
            "Sensitivity",
            "Monthly Detail",
            "Assumptions",
        }
        assumptions = sheets["Assumptions"]
        assert {"参数", "默认值", "来源", "可信度"}.issubset(assumptions.columns)
        assert len(assumptions) >= 8
        assert assumptions["参数"].str.contains("LLP shipset", regex=False).any()
        assert assumptions["默认值"].astype(str).str.contains("12%").any()
        assert assumptions["参数"].str.contains("相对价值分摊", regex=False).any()
        assert "Implied Discount Rate" in summary.columns
