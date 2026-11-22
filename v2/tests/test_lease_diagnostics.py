"""模块①扩展测试：IRR 解释与可视化仪表盘。"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import pytest
from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lease_returns import (  # noqa: E402
    build_irr_explanation,
    calculate_returns,
    write_returns_report,
)
from config_loader import load_config  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return load_config()


class TestIrrDiagnostics:
    def test_irr_above_fifteen_percent_prints_valuation_explanation(self, cfg):
        diagnostic = build_irr_explanation(cfg, 0.16)

        assert diagnostic is not None
        assert diagnostic["Reference Status"] == "Within reference range"
        assert diagnostic["Implied Discount Rate"] == pytest.approx(0.0887497702)
        assert diagnostic["Deviation vs CMV"] == pytest.approx(0.1166, abs=1e-4)
        assert "隐含折现率" in diagnostic["Explanation"]
        assert "不视为失败" in diagnostic["Explanation"]

    def test_irr_below_reference_range_also_explains(self, cfg):
        diagnostic = build_irr_explanation(cfg, 0.09)

        assert diagnostic is not None
        assert diagnostic["Reference Status"] == "Below reference range"

    def test_irr_at_fifteen_percent_does_not_trigger(self, cfg):
        assert build_irr_explanation(cfg, 0.15) is None


class TestLeaseDashboard:
    def test_report_has_dashboard_charts_and_rent_warning(self, cfg, tmp_path):
        high_rent_cfg = replace(
            cfg, lease=replace(cfg.lease, lease_rate_factor=0.02)
        )
        summary, monthly = calculate_returns(high_rent_cfg)
        diagnostic = build_irr_explanation(
            high_rent_cfg, summary.loc["base", "Equity IRR"]
        )
        output_path = tmp_path / "returns_report.xlsx"

        write_returns_report(
            summary,
            monthly,
            output_path,
            cfg=high_rent_cfg,
            irr_explanation=diagnostic,
        )

        workbook = load_workbook(output_path)
        assert {"Dashboard", "Diagnostics", "Summary", "base", "downside", "severe"}.issubset(
            workbook.sheetnames
        )
        dashboard = workbook["Dashboard"]
        assert len(dashboard._charts) == 2
        values = [cell.value for row in dashboard.iter_rows() for cell in row]
        assert "租金合理性校验" in values
        assert any("警告" in str(value) for value in values)
        assert any("不视为失败" in str(value) for value in values)
