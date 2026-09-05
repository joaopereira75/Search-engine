"""
asymmetry_engine/research_pipeline.py

Orquestrador de investigação para o Asymmetry Engine.

Esta camada junta, sem alterar o core de valuation:

1. Validação já executada pelo load_case_config.
2. Execução do caso-base.
3. Sensitivities one-way configuradas por driver.
4. Break-even solvers configurados por tipo.
5. Ranking de drivers pela alteração de decisão, veto, retorno, assimetria e
   refinancing gap.

O pipeline não edita o YAML, nem muda o loaded_config fornecido. Todas as
variações são aplicadas a cópias profundas pelos engines subjacentes.

Exemplo:

    from asymmetry_engine.config import load_case_config
    from asymmetry_engine.research_pipeline import run_research_pipeline

    loaded = load_case_config("cases/wolf_2023_08_23.yaml")
    package = run_research_pipeline(loaded)

    print(package["base_case"]["decision"])
    print(package["driver_ranking"])

Para passar um plano explícito:

    plan = {
        "sensitivities": [
            {"driver": "debt_context.cash_usd", "values": [2e9, 3e9, 5e9, 6e9]},
            {"driver": "debt_context.annual_fcf_usd", "values": [-2e9, -1e9, 0]},
        ],
        "breakevens": [
            {"type": "cash-veto", "lower": 0, "upper": 10e9},
            {"type": "asp-feasibility", "scenario": "Bull", "lower": 0.01, "upper": 50},
        ],
    }
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Callable, Mapping, Optional, Sequence

from .breakeven_engine import (
    BreakEvenError,
    solve_maximum_fcf_burn_without_hard_veto,
    solve_minimum_asp_for_scenario_feasibility,
    solve_minimum_cash_to_remove_hard_veto,
    solve_minimum_value_for_decision,
)
from .config import run_case_config
from .sensitivity_engine import SensitivityError, run_one_way_sensitivity


RESEARCH_PIPELINE_VERSION = "RESEARCH-PIPELINE-1.0.0"


class ResearchPipelineError(ValueError):
    """Erro de plano de investigação ou de configuração do pipeline."""


def _as_mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResearchPipelineError(f"{label} must be a mapping/object.")
    return value


def _as_list(value: Any, label: str) -> list[Any]:
    if not isinstance(value, list):
        raise ResearchPipelineError(f"{label} must be a list.")
    return value


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        value = value.item() if hasattr(value, "item") else value
        return float(value)
    except (TypeError, ValueError):
        return None


def _driver_label(driver: str) -> str:
    labels = {
        "financials.cash_usd": "Valuation cash",
        "financials.total_debt_usd": "Total debt",
        "factory.asp_usd": "ASP",
        "factory.capacity_max_units": "Maximum capacity",
        "factory.current_utilization": "Utilisation",
        "debt_context.cash_usd": "Cash at maturity analysis",
        "debt_context.annual_fcf_usd": "Annual FCF burn",
        "valuation.wacc": "WACC",
        "valuation.wacc_terminal": "Terminal WACC",
    }
    if driver in labels:
        return labels[driver]
    if driver.startswith("scenarios."):
        parts = driver.split(".")
        if len(parts) == 3:
            return f"{parts[1]} scenario {parts[2]}"
    return driver


def default_research_plan(loaded_config: Mapping[str, Any]) -> dict[str, Any]:
    """
    Constrói plano conservador a partir dos inputs que existem no caso.

    Não inventa drivers obrigatórios. Por exemplo, só propõe cash/FCF debt
    sensitivities se existir debt_context; só propõe ASP/capacidade se o
    negócio estiver classificado como fab.
    """
    financials = loaded_config.get("financials")
    factory = loaded_config.get("factory")
    debt_context = loaded_config.get("debt_context")
    scenarios = loaded_config.get("scenarios", [])

    sensitivities: list[dict[str, Any]] = []
    breakevens: list[dict[str, Any]] = []

    if debt_context is not None:
        cash = _number(getattr(debt_context, "get", lambda *_: None)("cash_usd")) if isinstance(debt_context, Mapping) else None
        if cash is not None and cash >= 0:
            sensitivities.append({
                "driver": "debt_context.cash_usd",
                "values": [max(0.0, cash * factor) for factor in (0.5, 0.75, 1.0, 1.5, 2.0, 2.5)],
            })
            breakevens.append({
                "type": "cash-veto",
                "lower": 0.0,
                "upper": max(1_000_000.0, cash * 4.0),
                "resolution": 1_000.0,
            })

        annual_fcf = _number(getattr(debt_context, "get", lambda *_: None)("annual_fcf_usd")) if isinstance(debt_context, Mapping) else None
        if annual_fcf is not None:
            lower = min(annual_fcf * 1.5, annual_fcf - 1.0)
            sensitivities.append({
                "driver": "debt_context.annual_fcf_usd",
                "values": [lower, annual_fcf, annual_fcf * 0.75, annual_fcf * 0.5, 0.0],
            })
            breakevens.append({
                "type": "fcf-veto",
                "lower": lower,
                "upper": 0.0,
                "resolution": 1_000.0,
            })

    if factory is not None and getattr(factory, "business_model", None) == "fab":
        asp = _number(getattr(factory, "asp_usd", None))
        if asp is not None and asp > 0:
            sensitivities.append({
                "driver": "factory.asp_usd",
                "values": [asp * factor for factor in (0.6, 0.8, 1.0, 1.25, 1.5, 2.0)],
            })
            bull_names = [
                str(getattr(scenario, "name", ""))
                for scenario in scenarios
                if "bull" in str(getattr(scenario, "name", "")).lower()
            ]
            if bull_names:
                breakevens.append({
                    "type": "asp-feasibility",
                    "scenario": bull_names[0],
                    "lower": max(0.0001, asp * 0.1),
                    "upper": asp * 10.0,
                    "resolution": 0.001,
                })

        capacity = _number(getattr(factory, "capacity_max_units", None))
        current_capacity = _number(getattr(factory, "current_capacity_units", None))
        if capacity is not None and capacity > 0:
            lower_capacity = max(capacity, current_capacity or 0.0)
            sensitivities.append({
                "driver": "factory.capacity_max_units",
                "values": [lower_capacity, lower_capacity * 1.25, lower_capacity * 1.5, lower_capacity * 2.0],
            })

    if financials is not None:
        total_debt = _number(getattr(financials, "total_debt_usd", None))
        if total_debt is not None and total_debt >= 0:
            sensitivities.append({
                "driver": "financials.total_debt_usd",
                "values": [total_debt * factor for factor in (0.5, 0.75, 1.0, 1.25, 1.5)],
            })

    for scenario in scenarios:
        name = str(getattr(scenario, "name", ""))
        probability = _number(getattr(scenario, "probability", None))
        margin = _number(getattr(scenario, "ebit_margin", None))
        if "base" in name.lower() and margin is not None:
            lower_margin = max(-0.95, margin - 0.10)
            upper_margin = min(1.0, margin + 0.15)
            sensitivities.append({
                "driver": f"scenarios.{name}.ebit_margin",
                "values": [lower_margin, margin, upper_margin],
            })
        if probability is not None and probability < 0:
            raise ResearchPipelineError("Scenario probability must not be negative.")

    return {"sensitivities": sensitivities, "breakevens": breakevens}


def _run_breakeven(
    loaded_config: Mapping[str, Any],
    specification: Mapping[str, Any],
    runner: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> dict[str, Any]:
    kind = str(specification.get("type", "")).strip()
    if not kind:
        raise ResearchPipelineError("Each breakeven item requires a non-empty 'type'.")

    lower = specification.get("lower")
    upper = specification.get("upper")
    resolution = specification.get("resolution")

    try:
        if kind == "cash-veto":
            return solve_minimum_cash_to_remove_hard_veto(
                loaded_config,
                lower=lower,
                upper=upper,
                hard_veto=str(specification.get("hard_veto", "capital_structure_crisis_risk")),
                cash_driver=str(specification.get("driver", "debt_context.cash_usd")),
                resolution=resolution if resolution is not None else 1_000.0,
                runner=runner,
            )
        if kind == "fcf-veto":
            return solve_maximum_fcf_burn_without_hard_veto(
                loaded_config,
                lower=lower,
                upper=upper,
                hard_veto=str(specification.get("hard_veto", "capital_structure_crisis_risk")),
                fcf_driver=str(specification.get("driver", "debt_context.annual_fcf_usd")),
                resolution=resolution if resolution is not None else 1_000.0,
                runner=runner,
            )
        if kind == "asp-feasibility":
            scenario = specification.get("scenario")
            if not isinstance(scenario, str) or not scenario.strip():
                raise ResearchPipelineError("asp-feasibility requires a non-empty 'scenario'.")
            return solve_minimum_asp_for_scenario_feasibility(
                loaded_config,
                scenario_name=scenario,
                lower=lower,
                upper=upper,
                asp_driver=str(specification.get("driver", "factory.asp_usd")),
                resolution=resolution if resolution is not None else 0.001,
                runner=runner,
            )
        if kind == "decision":
            driver = specification.get("driver")
            if not isinstance(driver, str) or not driver.strip():
                raise ResearchPipelineError("decision break-even requires a non-empty 'driver'.")
            decisions = specification.get("acceptable_decisions", ("PILOT CANDIDATE", "CORE CANDIDATE"))
            if not isinstance(decisions, (list, tuple)):
                raise ResearchPipelineError("acceptable_decisions must be a list or tuple.")
            return solve_minimum_value_for_decision(
                loaded_config,
                driver=driver,
                lower=lower,
                upper=upper,
                acceptable_decisions=tuple(str(value) for value in decisions),
                resolution=resolution,
                runner=runner,
            )
    except (BreakEvenError, SensitivityError) as exc:
        raise ResearchPipelineError(f"Break-even '{kind}' failed: {exc}") from exc

    raise ResearchPipelineError(
        "Unknown breakeven type. Use cash-veto, fcf-veto, asp-feasibility or decision."
    )


def _driver_impact(sensitivity: Mapping[str, Any]) -> dict[str, Any]:
    points = sensitivity.get("points", [])
    baseline = sensitivity.get("baseline", {})
    if not points:
        return {
            "driver": sensitivity.get("driver"),
            "label": _driver_label(str(sensitivity.get("driver", ""))),
            "impact_score": 0.0,
            "decision_flip_count": 0,
            "hard_veto_flip_count": 0,
            "expected_return_range": None,
            "asymmetry_range": None,
            "refinancing_gap_range_usd": None,
        }

    scores = [_number(point.get("impact_score")) or 0.0 for point in points]
    returns = [_number(point.get("expected_return")) for point in points]
    asymmetries = [_number(point.get("asymmetry_ratio")) for point in points]
    gaps = [_number(point.get("refinancing_gap_usd")) for point in points]

    def value_range(values: Sequence[Optional[float]]) -> Optional[float]:
        present = [value for value in values if value is not None]
        return max(present) - min(present) if present else None

    baseline_vetoes = list(baseline.get("hard_vetoes", []))
    decision_flips = sum(point.get("decision") != baseline.get("decision") for point in points)
    veto_flips = sum(list(point.get("hard_vetoes", [])) != baseline_vetoes for point in points)

    return {
        "driver": sensitivity.get("driver"),
        "label": _driver_label(str(sensitivity.get("driver", ""))),
        "impact_score": round(max(scores), 6),
        "decision_flip_count": decision_flips,
        "hard_veto_flip_count": veto_flips,
        "expected_return_range": value_range(returns),
        "asymmetry_range": value_range(asymmetries),
        "refinancing_gap_range_usd": value_range(gaps),
    }


def rank_sensitivity_drivers(sensitivities: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Ordena drivers, privilegiando flips de decisão e de hard veto."""
    ranking = [_driver_impact(sensitivity) for sensitivity in sensitivities]
    ranking.sort(
        key=lambda item: (
            int(item["decision_flip_count"] > 0),
            int(item["hard_veto_flip_count"] > 0),
            float(item["impact_score"]),
        ),
        reverse=True,
    )
    for index, item in enumerate(ranking, start=1):
        item["rank"] = index
        score = float(item["impact_score"])
        if item["decision_flip_count"] or item["hard_veto_flip_count"] or score >= 100:
            item["materiality"] = "HIGH"
        elif score >= 25:
            item["materiality"] = "MEDIUM"
        else:
            item["materiality"] = "LOW"
    return ranking


def _normalise_plan(plan: Optional[Mapping[str, Any]], loaded_config: Mapping[str, Any]) -> dict[str, Any]:
    if plan is None:
        return default_research_plan(loaded_config)
    plan = _as_mapping(plan, "plan")
    sensitivities = _as_list(plan.get("sensitivities", []), "plan.sensitivities")
    breakevens = _as_list(plan.get("breakevens", []), "plan.breakevens")
    return {"sensitivities": sensitivities, "breakevens": breakevens}


def run_research_pipeline(
    loaded_config: Mapping[str, Any],
    *,
    plan: Optional[Mapping[str, Any]] = None,
    runner: Optional[Callable[[Mapping[str, Any]], Mapping[str, Any]]] = None,
    continue_on_error: bool = True,
) -> dict[str, Any]:
    """
    Executa o pacote de investigação completo e devolve output serializável.

    ``continue_on_error=True`` é adequado para research: uma sensitivity que
    não se aplica não deve apagar resultados válidos de outros drivers. Os
    erros são guardados em ``research_errors`` para auditoria.
    """
    if not isinstance(loaded_config, Mapping):
        raise ResearchPipelineError("loaded_config must be returned by load_case_config().")

    run = runner or run_case_config
    effective_plan = _normalise_plan(plan, loaded_config)
    base_case = dict(run(deepcopy(loaded_config)))
    sensitivity_results: list[dict[str, Any]] = []
    breakeven_results: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for index, raw_specification in enumerate(effective_plan["sensitivities"]):
        try:
            specification = _as_mapping(raw_specification, f"plan.sensitivities[{index}]")
            driver = specification.get("driver")
            values = specification.get("values")
            if not isinstance(driver, str) or not driver.strip():
                raise ResearchPipelineError("Sensitivity requires a non-empty 'driver'.")
            if not isinstance(values, list) or not values:
                raise ResearchPipelineError("Sensitivity requires a non-empty 'values' list.")
            sensitivity_results.append(
                run_one_way_sensitivity(
                    loaded_config,
                    driver=driver,
                    values=values,
                    runner=run,
                )
            )
        except (ResearchPipelineError, SensitivityError, ValueError) as exc:
            if not continue_on_error:
                raise ResearchPipelineError(f"Sensitivity {index} failed: {exc}") from exc
            errors.append({"stage": "sensitivity", "item": str(index), "error": str(exc)})

    for index, raw_specification in enumerate(effective_plan["breakevens"]):
        try:
            specification = _as_mapping(raw_specification, f"plan.breakevens[{index}]")
            breakeven_results.append(_run_breakeven(loaded_config, specification, run))
        except (ResearchPipelineError, BreakEvenError, SensitivityError, ValueError) as exc:
            if not continue_on_error:
                raise ResearchPipelineError(f"Break-even {index} failed: {exc}") from exc
            errors.append({"stage": "breakeven", "item": str(index), "error": str(exc)})

    return {
        "research_pipeline_version": RESEARCH_PIPELINE_VERSION,
        "case": dict(loaded_config.get("case", {})),
        "validation": dict(loaded_config.get("validation", {})),
        "base_case": base_case,
        "research_plan": deepcopy(effective_plan),
        "sensitivities": sensitivity_results,
        "driver_ranking": rank_sensitivity_drivers(sensitivity_results),
        "breakevens": breakeven_results,
        "research_errors": errors,
    }
