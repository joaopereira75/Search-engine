"""Testes de regressão para asymmetry_engine.validation (Validation v1)."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from asymmetry_engine.config import load_case_config
from asymmetry_engine.validation import HARD_FAIL, PASS, validate_case_raw


def _valid_case() -> dict:
    return {
        "case": {
            "company_name": "Validation Test Co.",
            "ticker": "VTEST",
            "point_in_time": "2024-01-31",
            "currency": "USD",
        },
        "financials": {
            "share_price_usd": 10.0,
            "current_shares": 10_000_000,
            "market_cap_usd": 100_000_000,
            "cash_usd": 25_000_000,
            "total_debt_usd": 15_000_000,
            "current_revenue_usd": 20_000_000,
            "nol_balance_usd": 0.0,
        },
        "factory": {
            "business_model": "fab",
            "capacity_max_units": 1_000_000,
            "current_capacity_units": 600_000,
            "current_utilization": 0.75,
            "yield_rate": 0.90,
            "asp_usd": 50.0,
            "expansion_capacity_units": 400_000,
            "expansion_lead_time_years": 1.0,
            "qualification_lead_time_years": 0.5,
            "ramp_years": 1.0,
            "variable_cost_per_unit": 25.0,
        },
        "valuation": {
            "wacc": 0.12,
            "wacc_initial": 0.15,
            "wacc_terminal": 0.10,
            "forecast_years": 5,
            "tax_rate": 0.21,
            "terminal_growth": 0.02,
            "target_ebit_margin": 0.20,
            "reinvestment_rate": 0.30,
        },
        "scenarios": [
            {
                "name": "Bear",
                "probability": 0.25,
                "revenue_cagr": -0.10,
                "ebit_margin": 0.05,
                "reinvestment_rate": 0.40,
                "exit_multiple": 8.0,
            },
            {
                "name": "Base",
                "probability": 0.50,
                "revenue_cagr": 0.20,
                "ebit_margin": 0.15,
                "reinvestment_rate": 0.30,
                "exit_multiple": 12.0,
            },
            {
                "name": "Bull",
                "probability": 0.25,
                "revenue_cagr": 0.40,
                "ebit_margin": 0.25,
                "reinvestment_rate": 0.20,
                "exit_multiple": 18.0,
            },
        ],
        "sources": [
            {
                "field": "share_price_usd",
                "value": 10.0,
                "source": "Synthetic test input",
                "as_of": "2024-01-31",
                "confidence": "high",
                "unit": "USD per share",
                "currency": "USD",
            },
            {
                "field": "current_shares",
                "value": 10_000_000,
                "source": "Synthetic test input",
                "as_of": "2024-01-31",
                "confidence": "high",
                "unit": "shares",
                "currency": "USD",
            },
            {
                "field": "market_cap_usd",
                "value": 100_000_000,
                "source": "Calculated price times shares",
                "as_of": "2024-01-31",
                "confidence": "high",
                "unit": "USD",
                "currency": "USD",
            },
            {
                "field": "cash_usd",
                "value": 25_000_000,
                "source": "Synthetic test input",
                "as_of": "2024-01-31",
                "confidence": "high",
                "unit": "USD",
                "currency": "USD",
            },
            {
                "field": "total_debt_usd",
                "value": 15_000_000,
                "source": "Synthetic test input",
                "as_of": "2024-01-31",
                "confidence": "high",
                "unit": "USD",
                "currency": "USD",
            },
            {
                "field": "current_revenue_usd",
                "value": 20_000_000,
                "source": "Synthetic test input",
                "as_of": "2024-01-31",
                "confidence": "high",
                "unit": "USD",
                "currency": "USD",
            },
            {
                "field": "factory_proxy",
                "value": "Synthetic factory assumptions",
                "source": "Synthetic test input",
                "as_of": "2024-01-31",
                "confidence": "medium",
                "unit": "units and USD",
                "currency": "USD",
            },
            {
                "field": "scenario_assumptions",
                "value": "Synthetic scenario assumptions",
                "source": "Synthetic test input",
                "as_of": "2024-01-31",
                "confidence": "medium",
                "unit": "assumptions",
                "currency": "USD",
            },
        ],
    }


def test_valid_case_passes_validation() -> None:
    result = validate_case_raw(_valid_case())

    assert result["status"] == PASS
    assert result["schema_errors"] == []
    assert result["financial_errors"] == []
    assert result["input_coverage"]["coverage_pct"] == 100.0


def test_probabilities_must_sum_to_one() -> None:
    case = _valid_case()
    case["scenarios"][0]["probability"] = 0.32

    result = validate_case_raw(case)

    assert result["status"] == HARD_FAIL
    assert any("must sum to 1.0" in error for error in result["financial_errors"])


def test_negative_probability_hard_fails() -> None:
    case = _valid_case()
    case["scenarios"][0]["probability"] = -0.01
    case["scenarios"][1]["probability"] = 0.76

    result = validate_case_raw(case)

    assert result["status"] == HARD_FAIL
    assert any("probability" in error and "between 0 and 1" in error for error in result["financial_errors"])


def test_duplicate_scenario_name_hard_fails() -> None:
    case = _valid_case()
    case["scenarios"][2]["name"] = "Base"

    result = validate_case_raw(case)

    assert result["status"] == HARD_FAIL
    assert any("duplicated" in error for error in result["financial_errors"])


def test_negative_cash_hard_fails() -> None:
    case = _valid_case()
    case["financials"]["cash_usd"] = -1.0

    result = validate_case_raw(case)

    assert result["status"] == HARD_FAIL
    assert "'cash_usd' must not be negative." in result["financial_errors"]


def test_yield_above_one_hard_fails() -> None:
    case = _valid_case()
    case["factory"]["yield_rate"] = 1.35

    result = validate_case_raw(case)

    assert result["status"] == HARD_FAIL
    assert "'yield_rate' must be between 0 and 1." in result["financial_errors"]


def test_negative_utilisation_hard_fails() -> None:
    case = _valid_case()
    case["factory"]["current_utilization"] = -0.10

    result = validate_case_raw(case)

    assert result["status"] == HARD_FAIL
    assert "'current_utilization' must be between 0 and 1." in result["financial_errors"]


def test_maturity_before_point_in_time_hard_fails() -> None:
    case = _valid_case()
    case["debt_structure"] = {
        "instruments": [
            {
                "principal_usd": 50_000_000,
                "conversion_price_usd": 20.0,
                "maturity_date": "2024-01-30",
                "coupon_rate": 0.02,
            }
        ]
    }
    case["debt_context"] = {
        "current_price_usd": 10.0,
        "current_date": "2024-01-31",
        "annual_fcf_usd": -2_000_000,
    }

    result = validate_case_raw(case)

    assert result["status"] == HARD_FAIL
    assert any("maturity_date" in error and "after the case point-in-time" in error for error in result["financial_errors"])


def test_non_positive_debt_principal_hard_fails() -> None:
    case = _valid_case()
    case["debt_structure"] = {
        "instruments": [
            {
                "principal_usd": 0,
                "conversion_price_usd": 20.0,
                "maturity_date": "2027-01-31",
                "coupon_rate": 0.02,
            }
        ]
    }
    case["debt_context"] = {
        "current_price_usd": 10.0,
        "current_date": "2024-01-31",
        "annual_fcf_usd": -2_000_000,
    }

    result = validate_case_raw(case)

    assert result["status"] == HARD_FAIL
    assert "'principal_usd' must be greater than zero." in result["financial_errors"]


def test_market_cap_reconciliation_is_warning_not_hard_fail() -> None:
    case = _valid_case()
    case["financials"]["market_cap_usd"] = 80_000_000

    result = validate_case_raw(case)

    assert result["status"] == PASS
    assert any("market_cap_usd differs" in warning for warning in result["economic_warnings"])


def test_source_after_point_in_time_is_warning() -> None:
    case = _valid_case()
    case["sources"][0]["as_of"] = "2024-02-01"

    result = validate_case_raw(case)

    assert result["status"] == PASS
    assert any("after case.point_in_time" in warning for warning in result["economic_warnings"])


def test_missing_material_source_metadata_is_warning() -> None:
    case = _valid_case()
    case["sources"] = []

    result = validate_case_raw(case)

    assert result["status"] == PASS
    assert any("Material input 'cash_usd' has no source metadata" in warning for warning in result["economic_warnings"])
    assert result["input_coverage"]["coverage_pct"] == 0.0


def test_low_confidence_material_evidence_is_warning() -> None:
    case = _valid_case()
    case["sources"][3]["confidence"] = "low"

    result = validate_case_raw(case)

    assert result["status"] == PASS
    assert any("'cash_usd' has low confidence" in warning for warning in result["economic_warnings"])


def test_load_case_config_rejects_invalid_yaml(tmp_path: Path) -> None:
    yaml_path = tmp_path / "invalid_case.yaml"
    yaml_path.write_text(
        """
case:
  company_name: Invalid Co
  ticker: BAD
  point_in_time: 2024-01-31
  currency: USD
financials:
  cash_usd: -10
factory:
  capacity_max_units: 100
  current_utilization: 0.8
  yield_rate: 0.9
  asp_usd: 10
valuation:
  wacc: 0.12
  forecast_years: 5
  tax_rate: 0.21
scenarios:
  - name: Base
    probability: 1.0
    revenue_cagr: 0.10
    ebit_margin: 0.10
    reinvestment_rate: 0.30
    exit_multiple: 10.0
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="cash_usd"):
        load_case_config(yaml_path)


def test_existing_wolfspeed_case_remains_valid() -> None:
    project_root = Path(__file__).resolve().parents[2]
    wolf_path = project_root / "cases" / "wolf_2023_08_23.yaml"
    if not wolf_path.exists():
        pytest.skip("Wolfspeed YAML case is not available in this checkout.")

    loaded = load_case_config(wolf_path)

    assert loaded["validation"]["status"] == PASS
    assert loaded["case"]["ticker"] == "WOLF"
    assert loaded["validation"]["financial_errors"] == []


def test_validate_does_not_mutate_input() -> None:
    case = _valid_case()
    original = deepcopy(case)

    validate_case_raw(case)

    assert case == original
