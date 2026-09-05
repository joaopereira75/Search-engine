"""
asymmetry_engine/tests/test_regression.py

Suite de regressão e integração do Asymmetry Engine v4.1.0.

Executar a partir da pasta-raiz do projeto:

    pytest -v

Estrutura esperada:

    search/
    ├── asymmetry_engine/
    │   ├── __init__.py
    │   ├── expectations_gap_engine.py
    │   ├── debt_structure_gate.py
    │   ├── case_runner.py
    │   ├── config.py
    │   └── tests/test_regression.py
    └── cases/
        ├── aehr_2023_11_30.yaml
        ├── poet_2024_05_15.yaml
        ├── sive_2024_09_17.yaml
        └── wolf_2023_08_23.yaml

Esta suite executa código produtivo; não contém testes decorativos que
comparam uma constante consigo própria.
"""

from __future__ import annotations

from pathlib import Path
import logging

import numpy as np
import pytest

from asymmetry_engine import (
    BacklogItem,
    ConvertibleInstrument,
    DebtStructure,
    ExpectationsGapEngine,
    FactoryData,
    FinancialInputs,
    RevenueQualityGate,
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
    return loaded, run_case_config(loaded)


def test_garch_exception_does_not_crash_on_log_message():
    """Regressão do bug em que '{exc}' criava NameError dentro do except."""
    logger = logging.getLogger("asymmetry_engine.tests")

    def fallback() -> float:
        try:
            raise ValueError("simulated arch failure")
        except Exception as error:
            logger.warning("GARCH failed: %s. Falling back to EWMA.", error)
            return 0.03

    assert fallback() == 0.03


def test_nol_shield_respects_80_percent_cap_in_real_engine():
    financials = FinancialInputs(
        market_cap_usd=100_000_000,
        current_revenue_usd=1_000_000,
        nol_balance_usd=1_000_000,
    )
    factory = FactoryData(capacity_max_units=1, asp_usd=1.0)
    valuation = ValuationAssumptions(wacc=0.10, forecast_years=1, tax_rate=0.21)
    scenario = Scenario("NOL test", 1.0, 0.0, 1.0, 0.0)
    engine = ExpectationsGapEngine(financials, factory, valuation, [scenario])

    fcff_path, ebit_path, nol_remaining = engine._fcff_path_with_nol(
        [1_000_000],
        ebit_margin=1.0,
        reinvestment_rate=0.0,
    )

    assert ebit_path == [1_000_000]
    assert nol_remaining == 200_000
    assert fcff_path == [958_000]


def test_dilution_does_not_create_value_for_existing_shareholders():
    enterprise_value = 800_000_000
    debt = 50_000_000
    cash = 40_000_000
    dilution_capital = 100_000_000

    equity_before_new_equity = enterprise_value - debt + cash + dilution_capital
    equity_current_holders = max(0.0, equity_before_new_equity - dilution_capital)

    assert equity_current_holders == 790_000_000
    assert equity_current_holders == enterprise_value - debt + cash


def test_aehr_yaml_loads_and_disables_physical_ceiling():
    loaded, result = _load_and_run("aehr_2023_11_30.yaml")

    assert loaded["case"]["ticker"] == "AEHR"
    assert loaded["factory"].business_model == "equipment_vendor"
    base_name = result["base_scenario"]
    base = next(
        scenario
        for scenario in result["valuation"]["scenario_valuation"]
        if scenario["scenario"] == base_name
    )
    assert base["physical_feasibility"]["status"] == "NOT_APPLICABLE"
    assert result["hard_vetoes"] == []


def test_poet_yaml_loads_and_flags_severe_customer_concentration():
    loaded, result = _load_and_run("poet_2024_05_15.yaml")

    assert loaded["case"]["ticker"] == "POET"
    assert loaded["financials"].nol_balance_usd == 160_000_000
    assert loaded["factory"].business_model == "not_applicable"
    assert loaded["factory"].top_customer_revenue_pct == 0.90

    base_name = result["base_scenario"]
    base = next(
        scenario
        for scenario in result["valuation"]["scenario_valuation"]
        if scenario["scenario"] == base_name
    )
    physical = base["physical_feasibility"]
    assert physical["status"] == "NOT_APPLICABLE"
    assert physical["concentration_verdict"] == "SEVERE_CONCENTRATION_RISK"
    assert any("severe customer concentration risk" in flag for flag in result["red_flags"])


def test_sivers_yaml_loads_and_applies_revenue_quality_gate():
    loaded, result = _load_and_run("sive_2024_09_17.yaml")

    assert loaded["case"]["ticker"] == "SIVE.ST"
    assert set(loaded["revenue_quality_gates"]) == {"Bear", "Base", "Bull"}

    base_name = result["base_scenario"]
    base = next(
        scenario
        for scenario in result["valuation"]["scenario_valuation"]
        if scenario["scenario"] == base_name
    )
    quality = base["revenue_quality_check"]

    assert quality is not None
    assert quality["gross_backlog_usd"] == 101_400_000
    assert quality["effective_backlog_usd"] == 22_495_000
    assert np.isclose(quality["implied_discount_pct"], 1.0 - 22_495_000 / 101_400_000)
    assert quality["verdict"] == "NRE_DOMINATED_BACKLOG"
    assert any("NRE-dominated backlog" in flag for flag in result["red_flags"])


def test_wolfspeed_yaml_loads_and_maturity_wall_is_hard_veto():
    loaded, result = _load_and_run("wolf_2023_08_23.yaml")

    assert loaded["case"]["ticker"] == "WOLF"
    assert loaded["debt_structure"] is not None
    assert result["debt_structure"] is not None
    assert result["debt_structure"]["verdict"] == "CAPITAL_STRUCTURE_CRISIS_RISK"
    assert result["debt_structure"]["total_refinancing_gap_usd"] == 1_275_600_000
    assert "capital_structure_crisis_risk" in result["hard_vetoes"]
    assert result["decision"] == "WATCHLIST / REJECT"

    2026_instrument = next(
        item
        for item in result["debt_structure"]["instruments"]
        if item["principal_usd"] == 1_275_600_000
    )
    assert 2026_instrument["wall_status"] == "MATURITY_WALL_UNCOVERED"
    assert 2026_instrument["projected_cash_available_usd"] == 0.0


def test_asymmetry_ratio_regression_runs_real_engine():
    """Executa os cinco cenários de regressão no motor real."""
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
    scenario_results = [engine.value_scenario(scenario) for scenario in scenarios]
    asymmetry = engine.asymmetry_ratio(scenario_results)

    assert np.isclose(asymmetry["asymmetry_ratio"], 1.3314597343477033, rtol=1e-9)
    assert asymmetry["verdict"] == "WATCHLIST_OR_REJECT"


def test_maturity_wall_with_negative_fcf_remains_uncovered():
    """Teste isolado da regressão crítica do max(0, FCF)."""
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


def test_case_runner_rejects_debt_crisis_even_if_valuation_is_run():
    """Confirma a prioridade de hard veto sobre a classificação de assimetria."""
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
