"""Testes para asymmetry_engine.research_pipeline."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from asymmetry_engine.config import load_case_config
from asymmetry_engine.research_pipeline import (
    ResearchPipelineError,
    default_research_plan,
    rank_sensitivity_drivers,
    run_research_pipeline,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]
WOLF_CASE = PROJECT_ROOT / "cases" / "wolf_2023_08_23.yaml"


def _loaded_wolf_case():
    if not WOLF_CASE.exists():
        pytest.skip("Wolfspeed YAML case is not available in this checkout.")
    return load_case_config(WOLF_CASE)


def test_default_plan_contains_only_supported_wolfspeed_drivers() -> None:
    loaded = _loaded_wolf_case()

    plan = default_research_plan(loaded)
    drivers = {item["driver"] for item in plan["sensitivities"]}
    breakeven_types = {item["type"] for item in plan["breakevens"]}

    assert "debt_context.cash_usd" in drivers
    assert "debt_context.annual_fcf_usd" in drivers
    assert "factory.asp_usd" in drivers
    assert "factory.capacity_max_units" in drivers
    assert "financials.total_debt_usd" in drivers
    assert "scenarios.Base.ebit_margin" in drivers
    assert {"cash-veto", "fcf-veto", "asp-feasibility"}.issubset(breakeven_types)


def test_research_pipeline_returns_base_sensitivities_ranking_and_breakevens() -> None:
    loaded = _loaded_wolf_case()
    plan = {
        "sensitivities": [
            {
                "driver": "debt_context.cash_usd",
                "values": [2_954_900_000, 5_700_000_000],
            },
            {
                "driver": "factory.asp_usd",
                "values": [5.0, 12.0],
            },
        ],
        "breakevens": [
            {
                "type": "cash-veto",
                "lower": 0,
                "upper": 10_000_000_000,
                "resolution": 10_000,
            },
            {
                "type": "asp-feasibility",
                "scenario": "Bull",
                "lower": 0.01,
                "upper": 50.0,
                "resolution": 0.01,
            },
        ],
    }

    package = run_research_pipeline(loaded, plan=plan)

    assert package["base_case"]["decision"] == "WATCHLIST / REJECT"
    assert package["validation"]["status"] == "PASS"
    assert len(package["sensitivities"]) == 2
    assert len(package["driver_ranking"]) == 2
    assert len(package["breakevens"]) == 2
    assert package["research_errors"] == []
    assert package["breakevens"][0]["status"] == "SOLVED"
    assert package["breakevens"][1]["status"] == "SOLVED"


def test_research_pipeline_does_not_mutate_loaded_case() -> None:
    loaded = _loaded_wolf_case()
    original = deepcopy(loaded)
    plan = {
        "sensitivities": [
            {
                "driver": "debt_context.cash_usd",
                "values": [1_000_000_000, 6_400_000_000],
            }
        ],
        "breakevens": [
            {
                "type": "cash-veto",
                "lower": 0,
                "upper": 10_000_000_000,
                "resolution": 100_000,
            }
        ],
    }

    run_research_pipeline(loaded, plan=plan)

    assert loaded == original


def test_pipeline_continues_and_records_invalid_sensitivity() -> None:
    loaded = _loaded_wolf_case()
    plan = {
        "sensitivities": [
            {"driver": "financials.not_a_real_field", "values": [1.0, 2.0]},
            {"driver": "factory.asp_usd", "values": [5.0, 10.0]},
        ],
        "breakevens": [],
    }

    package = run_research_pipeline(loaded, plan=plan, continue_on_error=True)

    assert len(package["sensitivities"]) == 1
    assert package["sensitivities"][0]["driver"] == "factory.asp_usd"
    assert len(package["research_errors"]) == 1
    assert package["research_errors"][0]["stage"] == "sensitivity"


def test_pipeline_raises_for_invalid_sensitivity_when_continue_disabled() -> None:
    loaded = _loaded_wolf_case()
    plan = {
        "sensitivities": [
            {"driver": "financials.not_a_real_field", "values": [1.0, 2.0]},
        ],
        "breakevens": [],
    }

    with pytest.raises(ResearchPipelineError, match="Sensitivity 0 failed"):
        run_research_pipeline(loaded, plan=plan, continue_on_error=False)


def test_pipeline_continues_and_records_invalid_breakeven() -> None:
    loaded = _loaded_wolf_case()
    plan = {
        "sensitivities": [],
        "breakevens": [
            {"type": "not-a-real-break-even", "lower": 0, "upper": 1},
            {
                "type": "cash-veto",
                "lower": 0,
                "upper": 10_000_000_000,
                "resolution": 100_000,
            },
        ],
    }

    package = run_research_pipeline(loaded, plan=plan, continue_on_error=True)

    assert len(package["breakevens"]) == 1
    assert package["breakevens"][0]["status"] == "SOLVED"
    assert len(package["research_errors"]) == 1
    assert package["research_errors"][0]["stage"] == "breakeven"


def test_driver_ranking_prioritises_veto_flip_over_value_change() -> None:
    synthetic_sensitivities = [
        {
            "driver": "valuation.exit_multiple",
            "baseline": {
                "decision": "WATCHLIST / REJECT",
                "hard_vetoes": ["capital_structure_crisis_risk"],
            },
            "points": [
                {
                    "impact_score": 49.0,
                    "decision": "WATCHLIST / REJECT",
                    "hard_vetoes": ["capital_structure_crisis_risk"],
                    "expected_return": -0.50,
                    "asymmetry_ratio": 0.10,
                    "refinancing_gap_usd": 1_000_000,
                }
            ],
        },
        {
            "driver": "debt_context.cash_usd",
            "baseline": {
                "decision": "WATCHLIST / REJECT",
                "hard_vetoes": ["capital_structure_crisis_risk"],
            },
            "points": [
                {
                    "impact_score": 20.0,
                    "decision": "WATCHLIST / REJECT",
                    "hard_vetoes": [],
                    "expected_return": -0.80,
                    "asymmetry_ratio": 0.0,
                    "refinancing_gap_usd": 600_000,
                }
            ],
        },
    ]

    ranking = rank_sensitivity_drivers(synthetic_sensitivities)

    assert ranking[0]["driver"] == "debt_context.cash_usd"
    assert ranking[0]["hard_veto_flip_count"] == 1
    assert ranking[0]["materiality"] == "HIGH"
    assert ranking[1]["driver"] == "valuation.exit_multiple"


def test_default_pipeline_runs_without_fatal_error() -> None:
    loaded = _loaded_wolf_case()

    package = run_research_pipeline(loaded)

    assert package["research_pipeline_version"]
    assert package["base_case"]["decision"] == "WATCHLIST / REJECT"
    assert isinstance(package["sensitivities"], list)
    assert isinstance(package["driver_ranking"], list)
    assert isinstance(package["breakevens"], list)
    assert isinstance(package["research_errors"], list)
