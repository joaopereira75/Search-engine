"""
asymmetry_engine/breakeven_engine.py

Break-even / decision-flip solver para o Asymmetry Engine.

Usa bisection sobre uma configuração carregada, sempre através de cópias
profundas, para encontrar o limiar de um driver que satisfaz uma condição.
Não altera YAMLs nem o loaded_config original.

Exemplos:

    from asymmetry_engine.config import load_case_config
    from asymmetry_engine.breakeven_engine import (
        solve_minimum_cash_to_remove_hard_veto,
        solve_maximum_fcf_burn_without_hard_veto,
        solve_minimum_asp_for_scenario_feasibility,
    )

    loaded = load_case_config("cases/wolf_2023_08_23.yaml")

    cash = solve_minimum_cash_to_remove_hard_veto(
        loaded,
        lower=0,
        upper=10_000_000_000,
        hard_veto="capital_structure_crisis_risk",
    )

    burn = solve_maximum_fcf_burn_without_hard_veto(
        loaded,
        lower=-3_000_000_000,
        upper=0,
        hard_veto="capital_structure_crisis_risk",
    )

    asp = solve_minimum_asp_for_scenario_feasibility(
        loaded,
        scenario_name="Bull",
        lower=0.01,
        upper=50.0,
    )
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping, Optional

from .sensitivity_engine import (
    SensitivityError,
    extract_snapshot,
    get_driver_value,
    set_driver_value,
)


BREAKEVEN_VERSION = "BREAKEVEN-1.0.0"


class BreakEvenError(ValueError):
    """Erro de intervalo, condição ou monotonicidade do break-even solver."""


def _as_float(value: Any, label: str) -> float:
    if isinstance(value, bool) or value is None:
        raise BreakEvenError(f"{label} must be a finite number.")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise BreakEvenError(f"{label} must be a finite number.") from exc
    if number != number or number in (float("inf"), float("-inf")):
        raise BreakEvenError(f"{label} must be a finite number.")
    return number


def _run_candidate(
    loaded_config: Mapping[str, Any],
    driver: str,
    value: float,
    runner: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    candidate = deepcopy(loaded_config)
    set_driver_value(candidate, driver, value)
    result = runner(candidate)
    return extract_snapshot(result), result


def _default_runner(loaded_config: Mapping[str, Any]) -> Mapping[str, Any]:
    from .config import run_case_config
    return run_case_config(loaded_config)


def _normalise_resolution(lower: float, upper: float, resolution: Optional[float]) -> float:
    if resolution is not None:
        resolution = _as_float(resolution, "resolution")
        if resolution <= 0:
            raise BreakEvenError("resolution must be greater than zero.")
        return resolution
    span = abs(upper - lower)
    if span == 0:
        return 1e-9
    return max(span * 1e-7, 1e-8)


def solve_threshold(
    loaded_config: Mapping[str, Any],
    *,
    driver: str,
    lower: float,
    upper: float,
    passes: Callable[[Mapping[str, Any], Mapping[str, Any]], bool],
    direction: str = "increasing",
    resolution: Optional[float] = None,
    max_iterations: int = 100,
    label: str = "threshold",
    runner: Optional[Callable[[Mapping[str, Any]], Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """
    Resolve um limiar com pesquisa binária.

    Parameters
    ----------
    driver:
        Caminho de driver suportado pelo sensitivity engine.
    lower, upper:
        Intervalo fechado de pesquisa. ``lower`` tem de ser menor que ``upper``.
    passes:
        Função ``(snapshot, full_result) -> bool`` que define o limiar.
    direction:
        ``increasing`` encontra o menor x que passa, assumindo que a condição
        é False no limite inferior e True no superior.
        ``decreasing`` encontra o maior x que passa, assumindo que a condição
        é True no limite inferior e False no superior.
    """
    if not isinstance(loaded_config, Mapping):
        raise BreakEvenError("loaded_config must be a mapping returned by load_case_config().")
    if direction not in {"increasing", "decreasing"}:
        raise BreakEvenError("direction must be 'increasing' or 'decreasing'.")
    if not callable(passes):
        raise BreakEvenError("passes must be callable.")
    if not isinstance(max_iterations, int) or max_iterations <= 0:
        raise BreakEvenError("max_iterations must be a positive integer.")

    lower = _as_float(lower, "lower")
    upper = _as_float(upper, "upper")
    if lower >= upper:
        raise BreakEvenError("lower must be smaller than upper.")
    tolerance = _normalise_resolution(lower, upper, resolution)
    original_value = get_driver_value(loaded_config, driver)
    run = runner or _default_runner

    baseline_full = run(deepcopy(loaded_config))
    baseline_snapshot = extract_snapshot(baseline_full)
    lower_snapshot, lower_full = _run_candidate(loaded_config, driver, lower, run)
    upper_snapshot, upper_full = _run_candidate(loaded_config, driver, upper, run)
    lower_passes = bool(passes(lower_snapshot, lower_full))
    upper_passes = bool(passes(upper_snapshot, upper_full))

    if direction == "increasing":
        if lower_passes:
            return {
                "breakeven_version": BREAKEVEN_VERSION,
                "label": label,
                "status": "ALREADY_PASSES_AT_LOWER_BOUND",
                "driver": driver,
                "original_driver_value": original_value,
                "threshold_value": lower,
                "resolution": tolerance,
                "iterations": 0,
                "baseline": baseline_snapshot,
                "threshold": lower_snapshot,
                "lower_bound": lower_snapshot,
                "upper_bound": upper_snapshot,
            }
        if not upper_passes:
            return {
                "breakeven_version": BREAKEVEN_VERSION,
                "label": label,
                "status": "NO_SOLUTION_WITHIN_RANGE",
                "driver": driver,
                "original_driver_value": original_value,
                "threshold_value": None,
                "resolution": tolerance,
                "iterations": 0,
                "baseline": baseline_snapshot,
                "threshold": None,
                "lower_bound": lower_snapshot,
                "upper_bound": upper_snapshot,
            }
    else:
        if upper_passes:
            return {
                "breakeven_version": BREAKEVEN_VERSION,
                "label": label,
                "status": "ALREADY_PASSES_AT_UPPER_BOUND",
                "driver": driver,
                "original_driver_value": original_value,
                "threshold_value": upper,
                "resolution": tolerance,
                "iterations": 0,
                "baseline": baseline_snapshot,
                "threshold": upper_snapshot,
                "lower_bound": lower_snapshot,
                "upper_bound": upper_snapshot,
            }
        if not lower_passes:
            return {
                "breakeven_version": BREAKEVEN_VERSION,
                "label": label,
                "status": "NO_SOLUTION_WITHIN_RANGE",
                "driver": driver,
                "original_driver_value": original_value,
                "threshold_value": None,
                "resolution": tolerance,
                "iterations": 0,
                "baseline": baseline_snapshot,
                "threshold": None,
                "lower_bound": lower_snapshot,
                "upper_bound": upper_snapshot,
            }

    left = lower
    right = upper
    iterations = 0
    best_value = upper if direction == "increasing" else lower
    best_snapshot = upper_snapshot if direction == "increasing" else lower_snapshot

    while right - left > tolerance and iterations < max_iterations:
        midpoint = (left + right) / 2.0
        midpoint_snapshot, midpoint_full = _run_candidate(loaded_config, driver, midpoint, run)
        midpoint_passes = bool(passes(midpoint_snapshot, midpoint_full))
        iterations += 1

        if direction == "increasing":
            if midpoint_passes:
                right = midpoint
                best_value = midpoint
                best_snapshot = midpoint_snapshot
            else:
                left = midpoint
        else:
            if midpoint_passes:
                left = midpoint
                best_value = midpoint
                best_snapshot = midpoint_snapshot
            else:
                right = midpoint

    # Garante que o resultado devolvido satisfaz a condição, inclusive em
    # thresholds descontínuos causados por regras de veto.
    final_snapshot, final_full = _run_candidate(loaded_config, driver, best_value, run)
    if not passes(final_snapshot, final_full):
        raise BreakEvenError(
            f"Unable to verify '{label}' threshold. The supplied condition may not be monotonic within the range."
        )

    return {
        "breakeven_version": BREAKEVEN_VERSION,
        "label": label,
        "status": "SOLVED",
        "driver": driver,
        "original_driver_value": original_value,
        "threshold_value": best_value,
        "resolution": tolerance,
        "iterations": iterations,
        "baseline": baseline_snapshot,
        "threshold": final_snapshot,
        "lower_bound": lower_snapshot,
        "upper_bound": upper_snapshot,
    }


def solve_minimum_cash_to_remove_hard_veto(
    loaded_config: Mapping[str, Any],
    *,
    lower: float,
    upper: float,
    hard_veto: str = "capital_structure_crisis_risk",
    cash_driver: str = "debt_context.cash_usd",
    resolution: Optional[float] = 1_000.0,
    runner: Optional[Callable[[Mapping[str, Any]], Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Encontra o cash mínimo que remove um hard veto específico."""
    return solve_threshold(
        loaded_config,
        driver=cash_driver,
        lower=lower,
        upper=upper,
        passes=lambda snapshot, _: hard_veto not in snapshot.get("hard_vetoes", []),
        direction="increasing",
        resolution=resolution,
        label=f"minimum cash to remove {hard_veto}",
        runner=runner,
    )


def solve_maximum_fcf_burn_without_hard_veto(
    loaded_config: Mapping[str, Any],
    *,
    lower: float,
    upper: float,
    hard_veto: str = "capital_structure_crisis_risk",
    fcf_driver: str = "debt_context.annual_fcf_usd",
    resolution: Optional[float] = 1_000.0,
    runner: Optional[Callable[[Mapping[str, Any]], Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """
    Encontra o FCF mínimo aceitável (o burn mais negativo possível) sem veto.

    Exemplo de intervalo: lower=-3_000_000_000, upper=0. O resultado será o
    valor mais negativo que ainda deixa a condição passar.
    """
    return solve_threshold(
        loaded_config,
        driver=fcf_driver,
        lower=lower,
        upper=upper,
        passes=lambda snapshot, _: hard_veto not in snapshot.get("hard_vetoes", []),
        direction="decreasing",
        resolution=resolution,
        label=f"maximum FCF burn without {hard_veto}",
        runner=runner,
    )


def solve_minimum_asp_for_scenario_feasibility(
    loaded_config: Mapping[str, Any],
    *,
    scenario_name: str,
    lower: float,
    upper: float,
    asp_driver: str = "factory.asp_usd",
    resolution: Optional[float] = 0.001,
    runner: Optional[Callable[[Mapping[str, Any]], Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Encontra o ASP mínimo que torna um cenário fisicamente suportado."""

    def scenario_is_feasible(_: Mapping[str, Any], full_result: Mapping[str, Any]) -> bool:
        scenarios = full_result.get("valuation", {}).get("scenario_valuation", [])
        for scenario in scenarios:
            if str(scenario.get("scenario")) == scenario_name:
                status = scenario.get("physical_feasibility", {}).get("status")
                return status != "PHYSICALLY_UNSUPPORTED"
        raise BreakEvenError(f"Scenario '{scenario_name}' was not returned by the valuation engine.")

    return solve_threshold(
        loaded_config,
        driver=asp_driver,
        lower=lower,
        upper=upper,
        passes=scenario_is_feasible,
        direction="increasing",
        resolution=resolution,
        label=f"minimum ASP for {scenario_name} physical feasibility",
        runner=runner,
    )


def solve_minimum_value_for_decision(
    loaded_config: Mapping[str, Any],
    *,
    driver: str,
    lower: float,
    upper: float,
    acceptable_decisions: tuple[str, ...] = ("PILOT CANDIDATE", "CORE CANDIDATE"),
    resolution: Optional[float] = None,
    runner: Optional[Callable[[Mapping[str, Any]], Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """Encontra o menor valor de um driver que melhora a decisão para PILOT/CORE."""
    allowed = set(acceptable_decisions)
    if not allowed:
        raise BreakEvenError("acceptable_decisions must not be empty.")
    return solve_threshold(
        loaded_config,
        driver=driver,
        lower=lower,
        upper=upper,
        passes=lambda snapshot, _: snapshot.get("decision") in allowed,
        direction="increasing",
        resolution=resolution,
        label=f"minimum {driver} for investable decision",
        runner=runner,
    )
