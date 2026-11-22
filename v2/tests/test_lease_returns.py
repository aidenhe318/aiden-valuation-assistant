"""模块①测试：租赁收益测算 / Tests for lease returns.

覆盖 MODEL_SPEC 校验基准 #1、#2、#3、#8：
- 月度 XIRR 与手算样例误差小于 1e-6
- 等额本息摊还表首月及末期余额与手算一致
- 信用调整、三种摊还方式、租金合理性警告与三情景报告
"""

from __future__ import annotations

import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from config_loader import load_config  # noqa: E402
from lease_returns import (  # noqa: E402
    adjusted_lease_debt_terms,
    build_debt_schedule,
    calculate_returns,
    check_rent_reasonableness,
    monthly_rent_schedule,
    monthly_xirr,
    write_returns_report,
)  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return load_config()


class TestMonthlyXirr:
    def test_hand_calculated_two_cash_flows(self):
        """投入 100、一个月后回收 110，年化收益率应为 1.1^12 - 1。"""
        result = monthly_xirr([-100.0, 110.0])
        expected = 1.1**12 - 1.0
        assert result == pytest.approx(expected, abs=1e-6)

    def test_rejects_cash_flows_without_sign_change(self):
        with pytest.raises(ValueError, match="正负"):
            monthly_xirr([-100.0, -10.0])


class TestDebtSchedule:
    def test_monthly_12yr_first_and_final_balance(self):
        """首月利息 100；144 期等额本息末期余额归零。"""
        principal = 10_000.0
        monthly_rate = 0.12 / 12.0
        payment = principal * monthly_rate / (1.0 - (1.0 + monthly_rate) ** -144)

        schedule = build_debt_schedule(
            principal=principal,
            interest_rate_annual=0.12,
            term_months=144,
            amortization="monthly_12yr",
        )

        first_principal = payment - 100.0
        assert schedule.iloc[0]["debt_service"] == pytest.approx(payment, abs=1e-8)
        assert schedule.iloc[0]["ending_balance"] == pytest.approx(
            principal - first_principal, abs=1e-8
        )
        assert schedule.iloc[-1]["ending_balance"] == pytest.approx(0.0, abs=1e-6)

    def test_12yr_payment_is_not_compressed_into_lease_term(self):
        principal = 10_000.0
        monthly_rate = 0.12 / 12.0
        expected_payment = principal * monthly_rate / (
            1.0 - (1.0 + monthly_rate) ** -144
        )
        schedule = build_debt_schedule(
            principal=principal,
            interest_rate_annual=0.12,
            term_months=96,
            amortization="monthly_12yr",
        )
        assert schedule.iloc[0]["debt_service"] == pytest.approx(expected_payment)
        assert schedule.iloc[-1]["ending_balance"] > 0.0

    def test_bullet_repays_principal_at_maturity(self):
        schedule = build_debt_schedule(
            principal=100_000.0,
            interest_rate_annual=0.12,
            term_months=3,
            amortization="bullet",
        )
        assert schedule["principal_payment"].iloc[:2].tolist() == [0.0, 0.0]
        assert schedule.iloc[-1]["principal_payment"] == pytest.approx(100_000.0)
        assert schedule.iloc[-1]["ending_balance"] == pytest.approx(0.0, abs=1e-8)

    def test_sculpted_meets_target_dscr(self):
        cfads = pd.Series([1_150.0, 1_265.0], index=[1, 2])
        schedule = build_debt_schedule(
            principal=2_000.0,
            interest_rate_annual=0.12,
            term_months=2,
            amortization="sculpted",
            cfads=cfads,
            target_dscr=1.15,
        )
        dscr = cfads / schedule["debt_service"]
        assert np.all(dscr >= 1.15)
        assert schedule.iloc[-1]["ending_balance"] < 2_000.0


class TestLeaseTerms:
    def test_monthly_rent_escalation(self, cfg):
        escalated = replace(cfg, lease=replace(cfg.lease, escalation_pct=0.01))
        rents = monthly_rent_schedule(escalated)
        base_rent = cfg.lease.lease_rate_factor * cfg.lease.pricing_base_usd
        assert rents.iloc[0] == pytest.approx(base_rent)
        assert rents.iloc[1] == pytest.approx(base_rent * 1.01)

    def test_low_credit_score_caps_ltv_and_adds_spread(self, cfg):
        low_score = replace(
            cfg,
            lessee_credit=replace(cfg.lessee_credit, score=59.9),
        )
        lrf, ltv = adjusted_lease_debt_terms(low_score)
        assert ltv == pytest.approx(0.60)
        assert lrf == pytest.approx(cfg.lease.lease_rate_factor + 0.0010)

    def test_rent_reasonableness_warning(self, cfg, capsys):
        high_rent_cfg = replace(
            cfg,
            lease=replace(cfg.lease, lease_rate_factor=0.02),
        )
        rent = high_rent_cfg.lease.lease_rate_factor * high_rent_cfg.lease.pricing_base_usd
        assert not check_rent_reasonableness(high_rent_cfg, rent)
        assert "警告" in capsys.readouterr().out

    def test_default_rent_is_reasonable(self, cfg, capsys):
        rent = cfg.lease.lease_rate_factor * cfg.lease.pricing_base_usd
        assert check_rent_reasonableness(cfg, rent)
        assert capsys.readouterr().out == ""


class TestScenarioReturns:
    def test_three_scenarios_and_report(self, cfg, tmp_path):
        summary, monthly = calculate_returns(cfg)
        assert list(summary.index) == ["base", "downside", "severe"]
        assert {"Equity IRR", "MOIC", "Min DSCR"}.issubset(summary.columns)
        assert set(monthly) == {"base", "downside", "severe"}
        assert all(len(df) == cfg.lease.term_months for df in monthly.values())
        assert np.isfinite(summary["Equity IRR"]).all()
        assert (summary["MOIC"] > 0).all()

        output_path = tmp_path / "returns_report.xlsx"
        write_returns_report(summary, monthly, output_path)
        assert output_path.exists()
        sheets = pd.read_excel(output_path, sheet_name=None)
        assert "Summary" in sheets
        assert set(sheets) == {"Summary", "base", "downside", "severe"}
