"""task-06 测试：五家航司信用评分与租赁条款接入。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from airline_credit import (  # noqa: E402
    build_credit_comparison,
    load_airline_credit_inputs,
    write_credit_report,
)
from config_loader import load_config  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return load_config()


class TestAirlineCredit:
    def test_five_airline_anchor_scores_and_lufthansa_spelling(self, cfg):
        result = build_credit_comparison(cfg).set_index("Airline")
        expected = {
            "Ryanair": 66.55,
            "AirAsiaX": 51.55,
            "Delta": 50.75,
            "easyJet": 46.80,
            "Lufthansa": 43.60,
        }

        assert set(result.index) == set(expected)
        assert "Luthansa" not in result.index
        for airline, score in expected.items():
            assert result.loc[airline, "Credit Score"] == pytest.approx(score)

    def test_weighted_score_recomputes_from_raw_components(self, cfg):
        result = build_credit_comparison(cfg).set_index("Airline")
        ryanair = result.loc["Ryanair"]
        hand_score = 49.5 * 0.40 + 75.0 * 0.30 + 90.0 * 0.20 + 62.5 * 0.10

        assert ryanair["Credit Score"] == pytest.approx(hand_score, abs=1e-12)
        assert result["Credit Score"].to_numpy() == pytest.approx(
            result["Reference Base Score"].to_numpy(), abs=1e-9
        )
        assert result.loc["Ryanair", "Risk Band"] == "Moderate"
        assert result.loc["Lufthansa", "Risk Band"] == "High"

    def test_scores_flow_into_existing_ltv_and_lrf_rules(self, cfg):
        result = build_credit_comparison(cfg).set_index("Airline")

        assert result.loc["Ryanair", "Effective LTV"] == pytest.approx(0.70)
        assert result.loc["Ryanair", "Effective LRF"] == pytest.approx(0.0095)
        assert result.loc["AirAsiaX", "Effective LTV"] == pytest.approx(0.60)
        assert result.loc["AirAsiaX", "Effective LRF"] == pytest.approx(0.0105)
        assert result.loc["Lufthansa", "Monthly Rent USD"] == pytest.approx(252_000.0)

    def test_credit_report_contains_scores_and_methodology(self, cfg, tmp_path):
        result = build_credit_comparison(cfg)
        output_path = tmp_path / "airline_credit_report.xlsx"

        write_credit_report(result, output_path)

        assert output_path.exists()
        sheets = pd.read_excel(output_path, sheet_name=None)
        assert set(sheets) == {"Credit Scores", "Methodology"}
        methodology = sheets["Methodology"].astype(str)
        assert methodology.apply(
            lambda column: column.str.contains("40% / 30% / 20% / 10%", regex=False)
        ).any().any()
        assert methodology.apply(
            lambda column: column.str.contains("github.com/antoinejrnc", regex=False)
        ).any().any()
