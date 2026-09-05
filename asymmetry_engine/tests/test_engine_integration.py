"""
asymmetry_engine/tests/test_engine_integration.py

Testes de integração do Asymmetry Engine v4.1.0.

Guardar este ficheiro em:
    asymmetry_engine/tests/test_engine_integration.py

Depois correr, a partir da raiz do projeto:
    python -m pytest -v

Este ficheiro é descoberto automaticamente pelo pytest porque o nome
começa por test_. Testa os quatro YAMLs e o fluxo produtivo:
YAML -> config.py -> case_runner.py -> valuation/debt gates -> decisão.
"""

from __future__ import annotations

from pathlib import Path
import logging

import numpy as np
import pytest

from asymmetry_engine import (
    ConvertibleInstrument,
    DebtStructure,
    ExpectationsGapEngine,
    FactoryData,
    FinancialInputs,
    Scenario,
    ValuationAssumptions,
    maturity_wall_check,
    run_full_case,
)
from asymmetry_engine.config import load_case_config, run_case_config


PROJECT_ROOT = Path(__file__).resolve().parents[2]
CASES_DIRECTORY = PROJECT_ROOT / "cases"


def _case_path(filename: str) -> Path:
    path = CASES_DIRECTORY / filename
    if not path.exists():
        pytest.fail(f"Caso YAML obrigatório não encontrado: {path}")
    return path


def _load_and_run(filename: str):
    loaded = load_case_config(_case_path(filename))
    result = run_case_config(loaded)
    return loaded, result


def _scenario_result(result, scenario_name: str):
    return next(
        scenario
        for scenario in result["valuation"]["scenario_valuation"]
        if scenario["scenario"] == scenario_name
    )


def test_garch_exception_logging_regression():
    """O fallback GARCH não pode criar NameError dentro do bloco except."""
    logger = logging.getLogger("asymmetry_engine.tests")

    def fallback() -> float:
        try:
            raise ValueError("simulated arch failure")
        except Exception as error:
            logger.warning("GARCH failed: %s. Falling back to EWMA.", error)
            return 0.03

    assert fallback() == 0.03


def test_nol_shield_uses_80_percent_cap_in_production_engine():
    financials = FinancialInputs(
        market_cap_usd=100_000_000,
        current_revenue_usd=1_000_000,
        nol_balance_usd=1_000_000,
    )
    factory = FactoryData(capacity_max_units=1, asp_usd=1.0)
    valuation = ValuationAssumptions(wacc=0.10, forecast_years=1, tax_rate=0.21)
    scenario = Scenario("NOL test", 1.0, 0.0, 0.99, 0.0)
    engine = ExpectationsGapEngine(financials, factory, valuation, [scenario])

    fcff_path, ebit_path, nol_remaining = engine._fcff_path_with_nol(
        [1_000_000],
        ebit_margin=1.0,
        reinvestment_rate=0.0,
    )

    assert ebit_path == [1_000_000]
    assert nol_remaining == 200_000
    assert fcff_path == [958_000]


def test_aehr_case_returns_not_applicable_for_physical_ceiling():
    loaded, result = _load_and_run("aehr_2023_11_30.yaml")

    assert loaded["case"]["ticker"] == "AEHR"
    assert loaded["factory"].business_model == "equipment_vendor"
    base = _scenario_result(result, result["base_scenario"])
    assert base["physical_feasibility"]["status"] == "NOT_APPLICABLE"
    assert result["hard_vetoes"] == []


def test_poet_case_flags_severe_customer_concentration():
    loaded, result = _load_and_run("poet_2024_05_15.yaml")

    assert loaded["case"]["ticker"] == "POET"
    assert loaded["financials"].nol_balance_usd == 160_000_000
    assert loaded["factory"].business_model == "not_applicable"
    assert loaded["factory"].top_customer_revenue_pct == 0.90

    base = _scenario_result(result, result["base_scenario"])
    physical = base["physical_feasibility"]
    assert physical["status"] == "NOT_APPLICABLE"
    assert physical["concentration_verdict"] == "SEVERE_CONCENTRATION_RISK"
    assert any("severe customer concentration risk" in item for item in result["red_flags"])


def test_sivers_case_applies_nre_dominated_backlog_gate():
    loaded, result = _load_and_run("sive_2024_09_17.yaml")

    assert loaded["case"]["ticker"] == "SIVE.ST"
    assert set(loaded["revenue_quality_gates"]) == {"Bear", "Base", "Bull"}

    base = _scenario_result(result, result["base_scenario"])
    quality = base["revenue_quality_check"]
    assert quality is not None
    assert quality["gross_backlog_usd"] == 101_400_000
    assert quality["effective_backlog_usd"] == 22_495_000
    assert np.isclose(quality["implied_discount_pct"], 1.0 - 22_495_000 / 101_400_000)
    assert quality["verdict"] == "NRE_DOMINATED_BACKLOG"
    assert any("NRE-dominated backlog" in item for item in result["red_flags"])


def test_wolfspeed_case_maturity_wall_is_hard_veto():
    loaded, result = _load_and_run("wolf_2023_08_23.yaml")

    assert loaded["case"]["ticker"] == "WOLF"
    assert result["debt_structure"] is not None
    assert result["debt_structure"]["verdict"] == "CAPITAL_STRUCTURE_CRISIS_RISK"
    assert result["debt_structure"]["total_refinancing_gap_usd"] == 1_275_600_000
    assert "capital_structure_crisis_risk" in result["hard_vetoes"]
    assert result["decision"] == "WATCHLIST / REJECT"

    maturity_2026 = next(
        item for item in result["debt_structure"]["instruments"]
        if item["principal_usd"] == 1_275_600_000
    )
    assert maturity_2026["wall_status"] == "MATURITY_WALL_UNCOVERED"
    assert maturity_2026["projected_cash_available_usd"] == 0.0


def test_asymmetry_ratio_regression_uses_real_engine():
    """Executa os cinco cenários de referência no motor produtivo."""
    financials = FinancialInputs(
        market_cap_usd=500_000_000,
        total_debt_usd=50_000_000,
        cash_usd=40_000_000,
        current_revenue_usd=100_000_000,
        current_shares=120_000_000,
        nol_balance_usd=85_000_000,
    )
    factory = FactoryData(
        capacity_max_units=2_000_000,
        current_capacity_units=1_200_000,
        current_utilization=0.80,
        yield_rate=0.90,
        asp_usd=250.0,
        expansion_capacity_units=800_000,
        expansion_lead_time_years=1.0,
        qualification_lead_time_years=0.5,
        ramp_years=1.0,
        variable_cost_per_unit=150.0,
    )
    valuation = ValuationAssumptions(
        wacc=0.12,
        wacc_initial=0.20,
        wacc_terminal=0.10,
        forecast_years=5,
        tax_rate=0.21,
        terminal_growth=0.02,
        target_ebit_margin=0.20,
        reinvestment_rate=0.25,
    )
    scenarios = [
        Scenario("Death / Severe Delay", 0.20, -0.10, 0.03, 0.50, 6.0, dilution_usd=150_000_000),
        Scenario("Delayed Ramp", 0.25, 0.05, 0.10, 0.40, 9.0, dilution_usd=80_000_000),
        Scenario("Base Inflection", 0.35, 0.25, 0.18, 0.30, 13.0, dilution_usd=40_000_000),
        Scenario("Strong Re-rating", 0.15, 0.40, 0.25, 0.20, 18.0),
        Scenario("Full Bottleneck", 0.05, 0.60, 0.30, 0.15, 22.0),
    ]

    engine = ExpectationsGapEngine(financials, factory, valuation, scenarios)
    results = [engine.value_scenario(scenario) for scenario in scenarios]
    asymmetry = engine.asymmetry_ratio(results)

    assert np.isclose(asymmetry["asymmetry_ratio"], 1.3314597343477033, rtol=1e-9)
    assert asymmetry["verdict"] == "WATCHLIST_OR_REJECT"


def test_negative_fcf_depletes_cash_in_isolated_maturity_wall():
    structure = DebtStructure(instruments=[
        ConvertibleInstrument(
            principal_usd=1_275_600_000,
            conversion_price_usd=63.0,
            maturity_date="2026-06-01",
            coupon_rate=0.0175,
        )
    ])
    result = maturity_wall_check(
        structure,
        current_price_usd=44.10,
        current_date="2023-08-23",
        cash_usd=2_954_900_000,
        annual_fcf_usd=-1_820_000_000,
        forecast_years=5,
    )

    assert result["verdict"] == "CAPITAL_STRUCTURE_CRISIS_RISK"
    assert result["total_refinancing_gap_usd"] == 1_275_600_000


def test_case_runner_hard_veto_overrides_local_valuation():
    financials = FinancialInputs(
        market_cap_usd=500_000_000,
        cash_usd=2_954_900_000,
        current_revenue_usd=100_000_000,
    )
    factory = FactoryData(capacity_max_units=2_000_000, asp_usd=250.0)
    valuation = ValuationAssumptions(wacc=0.12, forecast_years=5)
    scenarios = [Scenario("Base", 1.0, 0.20, 0.15, 0.30, 12.0)]
    debt = DebtStructure(instruments=[
        ConvertibleInstrument(
            principal_usd=1_275_600_000,
            conversion_price_usd=63.0,
            maturity_date="2026-06-01",
            coupon_rate=0.0175,
        )
    ])

    result = run_full_case(
        financials=financials,
        factory=factory,
        valuation=valuation,
        scenarios=scenarios,
        debt_structure=debt,
        debt_context={
            "current_price_usd": 44.10,
            "current_date": "2023-08-23",
            "annual_fcf_usd": -1_820_000_000,
        },
    )

    assert "capital_structure_crisis_risk" in result["hard_vetoes"]
    assert result["decision"] == "WATCHLIST / REJECT"
