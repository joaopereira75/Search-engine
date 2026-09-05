"""Invariantes económicos do Asymmetry Engine executados no motor real.

Estes testes não avaliam se uma tese é boa. Protegem relações que devem
manter-se quando se altera apenas um driver, evitando regressões silenciosas
na matemática, nas cópias de configuração ou na política de gates.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from asymmetry_engine.breakeven_engine import solve_minimum_cash_to_remove_hard_veto
from asymmetry_engine.config import load_case_config, run_case_config
from asymmetry_engine.sensitivity_engine import run_one_way_sensitivity, set_driver_value


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WOLF_CASE = PROJECT_ROOT / "cases" / "wolf_2023_08_23.yaml"


def _loaded_wolf_case():
    if not WOLF_CASE.exists():
        pytest.skip("Wolfspeed YAML case is not available in this checkout.")
    return load_case_config(WOLF_CASE)


def _scenario_by_name(result: dict, name: str) -> dict:
    for scenario in result["valuation"]["scenario_valuation"]:
        if scenario["scenario"] == name:
            return scenario
    raise AssertionError(f"Scenario '{name}' was not returned.")


def _run_with_driver(loaded: dict, driver: str, value: float) -> dict:
    candidate = deepcopy(loaded)
    set_driver_value(candidate, driver, value)
    return run_case_config(candidate)


def test_more_debt_context_cash_never_increases_refinancing_gap() -> None:
    loaded = _loaded_wolf_case()
    output = run_one_way_sensitivity(
        loaded,
        driver="debt_context.cash_usd",
        values=[1_000_000_000, 2_954_900_000, 4_500_000_000, 5_700_000_000, 6_400_000_000],
    )
    gaps = [point["refinancing_gap_usd"] for point in output["points"]]

    assert all(gap is not None for gap in gaps)
    assert gaps == sorted(gaps, reverse=True)


def test_less_negative_fcf_never_increases_refinancing_gap() -> None:
    loaded = _loaded_wolf_case()
    output = run_one_way_sensitivity(
        loaded,
        driver="debt_context.annual_fcf_usd",
        values=[-2_200_000_000, -1_820_000_000, -1_400_000_000, -1_000_000_000, -500_000_000, 0],
    )
    gaps = [point["refinancing_gap_usd"] for point in output["points"]]

    assert all(gap is not None for gap in gaps)
    assert gaps == sorted(gaps, reverse=True)


def test_higher_asp_never_worsens_maximum_physical_gap() -> None:
    loaded = _loaded_wolf_case()
    output = run_one_way_sensitivity(
        loaded,
        driver="factory.asp_usd",
        values=[2.0, 3.0, 5.0, 8.0, 12.0, 20.0],
    )
    gaps = [point["physical_feasibility_gap"] for point in output["points"]]

    assert all(gap is not None for gap in gaps)
    assert gaps == sorted(gaps, reverse=True)


def test_higher_capacity_never_worsens_maximum_physical_gap() -> None:
    loaded = _loaded_wolf_case()

    # Wolfspeed tem current_capacity_units = 2.0B. A capacidade máxima nunca
    # pode ficar abaixo da capacidade instalada; valores menores são inputs
    # inválidos e devem continuar bloqueados pelo FactoryData.validate().
    output = run_one_way_sensitivity(
        loaded,
        driver="factory.capacity_max_units",
        values=[2_000_000_000, 2_500_000_000, 3_000_000_000, 4_000_000_000, 5_000_000_000],
    )
    gaps = [point["physical_feasibility_gap"] for point in output["points"]]

    assert all(gap is not None for gap in gaps)
    assert gaps == sorted(gaps, reverse=True)


def test_higher_base_ebit_margin_never_reduces_base_enterprise_value() -> None:
    loaded = _loaded_wolf_case()
    values = [0.05, 0.08, 0.12, 0.16, 0.20, 0.25]

    enterprise_values = [
        _scenario_by_name(
            _run_with_driver(loaded, "scenarios.Base.ebit_margin", value),
            "Base",
        )["enterprise_value_usd"]
        for value in values
    ]

    assert enterprise_values == sorted(enterprise_values)


def test_higher_total_debt_never_increases_bull_equity_value() -> None:
    loaded = _loaded_wolf_case()
    values = [2_000_000_000, 3_000_000_000, 4_175_100_000, 5_000_000_000, 6_000_000_000]

    equity_values = [
        _scenario_by_name(
            _run_with_driver(loaded, "financials.total_debt_usd", value),
            "Bull",
        )["equity_value_usd"]
        for value in values
    ]

    assert equity_values == sorted(equity_values, reverse=True)


def test_maturity_wall_transitions_from_uncovered_to_partial_to_covered() -> None:
    loaded = _loaded_wolf_case()
    values = [2_954_900_000, 5_700_000_000, 6_400_000_000]

    statuses = []
    for value in values:
        debt = _run_with_driver(loaded, "debt_context.cash_usd", value)["debt_structure"]
        near_term = next(
            instrument
            for instrument in debt["instruments"]
            if instrument["maturity_date"] == "2026-06-01"
        )
        statuses.append(near_term["wall_status"])

    assert statuses == [
        "MATURITY_WALL_UNCOVERED",
        "MATURITY_WALL_PARTIAL_COVERAGE",
        "MATURITY_WALL_COVERED",
    ]


def test_crisis_exit_threshold_has_partial_gap_and_no_hard_veto() -> None:
    loaded = _loaded_wolf_case()
    solution = solve_minimum_cash_to_remove_hard_veto(
        loaded,
        lower=0,
        upper=10_000_000_000,
        resolution=1_000.0,
    )

    assert solution["status"] == "SOLVED"
    assert solution["threshold_value"] is not None
    assert "capital_structure_crisis_risk" in solution["baseline"]["hard_vetoes"]
    assert "capital_structure_crisis_risk" not in solution["threshold"]["hard_vetoes"]
    assert solution["threshold"]["refinancing_gap_usd"] is not None
    assert solution["threshold"]["refinancing_gap_usd"] > 0
    assert "partial_maturity_wall_coverage" in solution["threshold"]["red_flags"]


def test_changing_debt_context_cash_does_not_change_valuation_cash() -> None:
    loaded = _loaded_wolf_case()
    baseline = run_case_config(loaded)
    stressed = _run_with_driver(loaded, "debt_context.cash_usd", 6_400_000_000)

    baseline_equity = baseline["valuation"]["probability_weighted_valuation"]["expected_equity_value_usd"]
    stressed_equity = stressed["valuation"]["probability_weighted_valuation"]["expected_equity_value_usd"]
    assert stressed_equity == baseline_equity


def test_changing_financial_cash_increases_valuation_equity_value() -> None:
    loaded = _loaded_wolf_case()
    output = run_one_way_sensitivity(
        loaded,
        driver="financials.cash_usd",
        values=[1_000_000_000, 2_954_900_000, 4_500_000_000],
    )

    equity_values = [point["expected_equity_value_usd"] for point in output["points"]]
    assert all(value is not None for value in equity_values)
    assert equity_values == sorted(equity_values)
