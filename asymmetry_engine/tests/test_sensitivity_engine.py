"""Testes para asymmetry_engine.sensitivity_engine."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from asymmetry_engine.config import load_case_config, run_case_config
from asymmetry_engine.sensitivity_engine import (
    SensitivityError,
    extract_snapshot,
    get_driver_value,
    run_one_way_sensitivity,
    set_driver_value,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WOLF_CASE = PROJECT_ROOT / "cases" / "wolf_2023_08_23.yaml"


def _loaded_wolf_case():
    if not WOLF_CASE.exists():
        pytest.skip("Wolfspeed YAML case is not available in this checkout.")
    return load_case_config(WOLF_CASE)


def test_get_driver_value_reads_existing_dataclass_field() -> None:
    loaded = _loaded_wolf_case()

    assert get_driver_value(loaded, "financials.cash_usd") == 2_954_900_000
    assert get_driver_value(loaded, "scenarios.Base.ebit_margin") == 0.12
    assert get_driver_value(loaded, "scenarios[1].ebit_margin") == 0.12


def test_set_driver_value_changes_only_copy() -> None:
    loaded = _loaded_wolf_case()
    candidate = deepcopy(loaded)

    set_driver_value(candidate, "financials.cash_usd", 4_500_000_000)
    set_driver_value(candidate, "scenarios.Bull.exit_multiple", 18.0)

    assert candidate["financials"].cash_usd == 4_500_000_000
    assert candidate["scenarios"][2].exit_multiple == 18.0
    assert loaded["financials"].cash_usd == 2_954_900_000
    assert loaded["scenarios"][2].exit_multiple == 14.0


def test_unknown_driver_raises_clear_error() -> None:
    loaded = _loaded_wolf_case()

    with pytest.raises(SensitivityError, match="Unsupported driver"):
        get_driver_value(loaded, "unknown.value")

    with pytest.raises(SensitivityError, match="unknown field"):
        get_driver_value(loaded, "financials.not_a_real_field")

    with pytest.raises(SensitivityError, match="unknown scenario"):
        get_driver_value(loaded, "scenarios.DoesNotExist.ebit_margin")


def test_sensitivity_rejects_empty_values() -> None:
    loaded = _loaded_wolf_case()

    with pytest.raises(SensitivityError, match="non-empty sequence"):
        run_one_way_sensitivity(loaded, driver="financials.cash_usd", values=[])


def test_cash_sensitivity_is_non_destructive_and_extracts_snapshots() -> None:
    loaded = _loaded_wolf_case()
    original_cash = loaded["financials"].cash_usd
    original_scenarios = deepcopy(loaded["scenarios"])

    output = run_one_way_sensitivity(
        loaded,
        driver="financials.cash_usd",
        values=[1_000_000_000, 2_954_900_000, 4_500_000_000],
    )

    assert output["analysis_type"] == "one_way"
    assert output["driver"] == "financials.cash_usd"
    assert output["baseline_driver_value"] == original_cash
    assert len(output["points"]) == 3
    assert loaded["financials"].cash_usd == original_cash
    assert loaded["scenarios"] == original_scenarios

    point = output["points"][0]
    for field in (
        "driver",
        "value",
        "expected_equity_value_usd",
        "expected_return",
        "asymmetry_ratio",
        "asymmetry_verdict",
        "decision",
        "hard_vetoes",
        "red_flags",
        "refinancing_gap_usd",
        "physical_feasibility_gap",
        "incremental_roic",
        "dominant_scenario",
        "decision_flip",
        "impact_score",
    ):
        assert field in point


def test_more_cash_does_not_increase_refinancing_gap() -> None:
    loaded = _loaded_wolf_case()

    output = run_one_way_sensitivity(
        loaded,
        driver="financials.cash_usd",
        values=[1_000_000_000, 2_954_900_000, 4_500_000_000],
    )
    gaps = [point["refinancing_gap_usd"] for point in output["points"]]

    assert all(gap is not None for gap in gaps)
    assert gaps[0] >= gaps[1] >= gaps[2]


def test_less_negative_fcf_does_not_increase_refinancing_gap() -> None:
    loaded = _loaded_wolf_case()

    output = run_one_way_sensitivity(
        loaded,
        driver="debt_context.annual_fcf_usd",
        values=[-2_200_000_000, -1_820_000_000, -1_000_000_000, -500_000_000, 0],
    )
    gaps = [point["refinancing_gap_usd"] for point in output["points"]]

    assert all(gap is not None for gap in gaps)
    assert gaps == sorted(gaps, reverse=True)


def test_higher_asp_does_not_worsen_physical_feasibility_gap() -> None:
    loaded = _loaded_wolf_case()

    output = run_one_way_sensitivity(
        loaded,
        driver="factory.asp_usd",
        values=[3.0, 5.0, 8.0, 12.0],
    )
    gaps = [point["physical_feasibility_gap"] for point in output["points"]]

    assert all(gap is not None for gap in gaps)
    assert gaps == sorted(gaps, reverse=True)


def test_snapshot_has_wolfspeed_baseline_hard_veto() -> None:
    loaded = _loaded_wolf_case()
    result = run_case_config(loaded)
    snapshot = extract_snapshot(result)

    assert snapshot["decision"] == "WATCHLIST / REJECT"
    assert "capital_structure_crisis_risk" in snapshot["hard_vetoes"]
    assert snapshot["refinancing_gap_usd"] == 1_275_600_000
    assert snapshot["dominant_scenario"] == "Bull"


def test_points_ranked_by_impact_are_descending() -> None:
    loaded = _loaded_wolf_case()

    output = run_one_way_sensitivity(
        loaded,
        driver="financials.cash_usd",
        values=[1_000_000_000, 2_954_900_000, 4_500_000_000],
    )
    scores = [point["impact_score"] for point in output["points_ranked_by_impact"]]

    assert scores == sorted(scores, reverse=True)


def test_sensitivity_runs_baseline_without_mutating_loaded_validation() -> None:
    loaded = _loaded_wolf_case()
    original = deepcopy(loaded)

    run_one_way_sensitivity(
        loaded,
        driver="scenarios.Base.ebit_margin",
        values=[0.08, 0.12, 0.18],
    )

    assert loaded["validation"] == original["validation"]
    assert loaded["financials"] == original["financials"]
    assert loaded["factory"] == original["factory"]
    assert loaded["valuation"] == original["valuation"]
    assert loaded["scenarios"] == original["scenarios"]
