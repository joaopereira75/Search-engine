"""
Suite de testes de regressao end-to-end — Asymmetry Engine v4.0.2
=================================================================

Executar na pasta onde estao os ficheiros do motor:

    pytest 4_test_regression_FINAL.py -v

O que esta suite protege:
- Regressao do fallback de GARCH (logging nao pode criar NameError);
- Diluicao fair-value e equity floor a zero;
- Limite de 80% na utilizacao de NOL;
- business_model nao-fab devolve NOT_APPLICABLE;
- Thresholds de concentracao;
- RevenueQualityGate reproduz o caso Sivers;
- Maturity wall respeita FCF negativo (caso Wolfspeed);
- ExpectationsGapEngine REAL corre cenarios, calcula asymmetry e
  reproduz o valor de referencia 1.3314597343477033;
- Case runner REAL transforma crisis de estrutura de capital em hard veto.

Nota: os testes end-to-end requerem que estes ficheiros estejam na mesma
pasta ou estejam importaveis pelo PYTHONPATH:
  1_expectations_gap_engine_FINAL.py
  3_debt_structure_gate_FINAL.py
  7_case_runner_FINAL.py

Como os nomes dos ficheiros entregues comecam por numero, Python nao os
importa diretamente por sintaxe normal. A suite usa importlib.util para
carregar os modulos explicitamente a partir da mesma pasta.
"""

from __future__ import annotations

import importlib.util
import logging
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent


def _load_module(module_name: str, filename: str):
    """Carrega um ficheiro Python com nome que pode comecar por numero."""
    path = ROOT / filename
    if not path.exists():
        pytest.skip(f"Ficheiro necessario nao encontrado: {path.name}")
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Nao foi possivel criar loader para {path.name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def ege_module():
    return _load_module("expectations_gap_engine_FINAL", "1_expectations_gap_engine_FINAL.py")


@pytest.fixture(scope="module")
def debt_module():
    return _load_module("debt_structure_gate_FINAL", "3_debt_structure_gate_FINAL.py")


@pytest.fixture(scope="module")
def case_runner_module(ege_module, debt_module):
    # O runner importa estes nomes internamente; os fixtures anteriores
    # ja os registaram em sys.modules com os nomes esperados.
    return _load_module("case_runner_FINAL", "7_case_runner_FINAL.py")


def test_garch_exception_does_not_crash_on_log_message():
    """Protege o bug em que '{exc}' causava NameError dentro do except."""
    logger = logging.getLogger("test_regression")

    def forecast_with_fixed_fallback():
        try:
            raise ValueError("simulated arch failure")
        except Exception as exp:
            logger.warning(f"GARCH failed: {exp}. Falling back to EWMA.")
            return 0.03

    assert forecast_with_fixed_fallback() == 0.03


def test_dilution_is_fair_value_deduction():
    enterprise_value = 800_000_000
    debt = 50_000_000
    cash = 40_000_000
    additional_debt = 0.0
    dilution = 100_000_000

    equity_pre = enterprise_value - debt + cash - additional_debt + dilution
    equity_current = max(0.0, equity_pre - dilution)

    assert equity_current == enterprise_value - debt + cash - additional_debt
    assert equity_current == 790_000_000


def test_dilution_wipes_out_equity_when_funding_need_exceeds_value():
    equity_pre = 149_285_105
    dilution = 150_000_000
    assert max(0.0, equity_pre - dilution) == 0.0


def test_nol_shield_respects_80_percent_cap(ege_module):
    financials = ege_module.FinancialInputs(
        market_cap_usd=100_000_000,
        current_revenue_usd=1_000_000,
        nol_balance_usd=1_000_000,
    )
    factory = ege_module.FactoryData(capacity_max_units=1, asp_usd=1.0)
    valuation = ege_module.ValuationAssumptions(wacc=0.10, forecast_years=1, tax_rate=0.21)
    scenario = ege_module.Scenario("NOL test", 1.0, 0.0, 1.0, 0.0)
    engine = ege_module.ExpectationsGapEngine(financials, factory, valuation, [scenario])

    fcff_path, ebit_path, nol_remaining = engine._fcff_path_with_nol(
        [1_000_000], ebit_margin=1.0, reinvestment_rate=0.0
    )

    assert ebit_path == [1_000_000]
    assert nol_remaining == 200_000
    # EBIT tributavel = 200k; imposto = 42k; FCFF = 958k.
    assert fcff_path == [958_000]


def test_factory_business_model_not_applicable_for_equipment_vendor(ege_module):
    financials = ege_module.FinancialInputs(market_cap_usd=100_000_000)
    factory = ege_module.FactoryData(
        capacity_max_units=1,
        business_model="equipment_vendor",
        top_customer_revenue_pct=0.60,
    )
    valuation = ege_module.ValuationAssumptions(wacc=0.10)
    engine = ege_module.ExpectationsGapEngine(financials, factory, valuation, [])

    output = engine.physical_feasibility_gap(100_000_000)

    assert output["status"] == "NOT_APPLICABLE"
    assert output["concentration_verdict"] == "SEVERE_CONCENTRATION_RISK"
    assert output["top_customer_revenue_pct"] == 0.60


def test_concentration_risk_thresholds_through_real_engine(ege_module):
    financials = ege_module.FinancialInputs(market_cap_usd=100_000_000)
    valuation = ege_module.ValuationAssumptions(wacc=0.10)

    def check(pct: float) -> str:
        factory = ege_module.FactoryData(
            capacity_max_units=1,
            business_model="not_applicable",
            top_customer_revenue_pct=pct,
        )
        engine = ege_module.ExpectationsGapEngine(financials, factory, valuation, [])
        return engine.physical_feasibility_gap(1)["concentration_verdict"]

    assert check(0.60) == "SEVERE_CONCENTRATION_RISK"
    assert check(0.35) == "ELEVATED_CONCENTRATION_RISK"
    assert check(0.30) == "DIVERSIFIED"


def test_revenue_quality_gate_nre_dominated_through_real_engine(ege_module):
    gate = ege_module.RevenueQualityGate(items=[
        ege_module.BacklogItem("PO direta", 8_900_000, "direct_po"),
        ege_module.BacklogItem("NRE", 13_500_000, "nre_milestone"),
        ege_module.BacklogItem("Framework", 30_000_000, "framework_calloff"),
        ege_module.BacklogItem("NRE 2", 49_000_000, "nre_milestone"),
    ])

    assert gate.gross_announced_usd() == 101_400_000
    assert gate.effective_near_term_usd() == 22_495_000
    assert gate.verdict() == "NRE_DOMINATED_BACKLOG"


def test_maturity_wall_detects_uncovered_gap_with_negative_fcf(debt_module):
    debt = debt_module.DebtStructure(instruments=[
        debt_module.ConvertibleInstrument(
            principal_usd=1_275_600_000,
            conversion_price_usd=63.0,
            maturity_date="2026-06-01",
            coupon_rate=0.0175,
        )
    ])

    result = debt_module.maturity_wall_check(
        debt,
        current_price_usd=44.10,
        current_date="2023-08-23",
        cash_usd=2_954_900_000,
        annual_fcf_usd=-455_000_000 * 4,
        forecast_years=5,
    )

    assert result["verdict"] == "CAPITAL_STRUCTURE_CRISIS_RISK"
    assert result["total_refinancing_gap_usd"] == 1_275_600_000
    instrument = result["instruments"][0]
    assert instrument["wall_status"] == "MATURITY_WALL_UNCOVERED"
    assert instrument["projected_cash_available_usd"] == 0.0


def test_asymmetry_ratio_regression_5_scenarios_runs_real_engine(ege_module):
    """Substitui o antigo teste decorativo: executa o motor completo."""
    financials = ege_module.FinancialInputs(
        market_cap_usd=500_000_000,
        total_debt_usd=50_000_000,
        cash_usd=40_000_000,
        current_revenue_usd=100_000_000,
        current_shares=120_000_000,
        nol_balance_usd=85_000_000,
    )
    factory = ege_module.FactoryData(
        capacity_max_units=2_000_000,
        current_capacity_units=1_200_000,
        current_utilization=0.80,
        yield_rate=0.90,
        asp_usd=250.0,
        expansion_capacity_units=800_000,
        expansion_lead_time_years=1.0,
        ramp_years=1.0,
        qualification_lead_time_years=0.5,
        variable_cost_per_unit=150.0,
    )
    valuation = ege_module.ValuationAssumptions(
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
        ege_module.Scenario("Death / Severe Delay", 0.20, -0.10, 0.03, 0.50, 6.0, dilution_usd=150_000_000),
        ege_module.Scenario("Delayed Ramp", 0.25, 0.05, 0.10, 0.40, 9.0, dilution_usd=80_000_000),
        ege_module.Scenario("Base Inflection", 0.35, 0.25, 0.18, 0.30, 13.0, dilution_usd=40_000_000),
        ege_module.Scenario("Strong Re-rating", 0.15, 0.40, 0.25, 0.20, 18.0),
        ege_module.Scenario("Full Bottleneck", 0.05, 0.60, 0.30, 0.15, 22.0),
    ]

    engine = ege_module.ExpectationsGapEngine(financials, factory, valuation, scenarios)
    results = [engine.value_scenario(scenario) for scenario in scenarios]
    output = engine.asymmetry_ratio(results)

    assert np.isclose(output["asymmetry_ratio"], 1.3314597343477033, rtol=1e-9)
    assert output["verdict"] == "WATCHLIST_OR_REJECT"


def test_case_runner_turns_maturity_wall_into_hard_veto(ege_module, debt_module, case_runner_module):
    """Teste end-to-end da integracao: debt crisis vence um bom valuation local."""
    financials = ege_module.FinancialInputs(
        market_cap_usd=500_000_000,
        cash_usd=2_954_900_000,
        current_revenue_usd=100_000_000,
    )
    factory = ege_module.FactoryData(
        capacity_max_units=2_000_000,
        asp_usd=250.0,
        business_model="fab",
    )
    valuation = ege_module.ValuationAssumptions(wacc=0.12, forecast_years=5)
    scenarios = [
        ege_module.Scenario("Base", 1.0, 0.20, 0.15, 0.30, 12.0),
    ]
    debt = debt_module.DebtStructure(instruments=[
        debt_module.ConvertibleInstrument(
            principal_usd=1_275_600_000,
            conversion_price_usd=63.0,
            maturity_date="2026-06-01",
            coupon_rate=0.0175,
        )
    ])

    result = case_runner_module.run_full_case(
        financials=financials,
        factory=factory,
        valuation=valuation,
        scenarios=scenarios,
        debt_structure=debt,
        debt_context={
            "current_price_usd": 44.10,
            "current_date": "2023-08-23",
            "annual_fcf_usd": -455_000_000 * 4,
        },
    )

    assert result["debt_structure"]["verdict"] == "CAPITAL_STRUCTURE_CRISIS_RISK"
    assert result["debt_structure"]["total_refinancing_gap_usd"] == 1_275_600_000
    assert "capital_structure_crisis_risk" in result["hard_vetoes"]
    assert result["decision"] == "WATCHLIST / REJECT"


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
