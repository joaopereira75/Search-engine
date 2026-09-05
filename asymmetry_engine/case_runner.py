"""
asymmetry_engine/case_runner.py

Case Runner / Decision Orchestrator — v4.2.0

Integra valuation, gates operacionais, estrutura de capital e risco de
liquidez opcional. A camada aplica política de decisão: os motores
subordinados devolvem factos e classificações, enquanto este runner decide
se esses sinais constituem hard veto, red flag ou watch item.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Sequence

from .debt_structure_gate import DebtStructure, maturity_wall_check
from .expectations_gap_engine import (
    EngineConfig,
    ExpectationsGapEngine,
    FactoryData,
    FinancialInputs,
    RevenueQualityGate,
    Scenario,
    ValuationAssumptions,
)


RUNNER_VERSION = "CASE-RUNNER-4.2.0"

HARD_VETO_CODES = frozenset({
    "capital_structure_crisis_risk",
    "base_scenario_physically_unsupported",
    "base_scenario_implausible_margin",
})


def _unique(values: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(values))


def _base_scenario_name(scenarios: Sequence[Scenario]) -> str:
    named_base = [scenario for scenario in scenarios if "base" in scenario.name.lower()]
    if named_base:
        return max(named_base, key=lambda scenario: scenario.probability).name
    return max(scenarios, key=lambda scenario: scenario.probability).name


def _classify_decision(
    *,
    asymmetry_verdict: str,
    hard_vetoes: Sequence[str],
    red_flags: Sequence[str],
) -> str:
    if hard_vetoes:
        return "WATCHLIST / REJECT"
    if asymmetry_verdict == "WATCHLIST_OR_REJECT":
        return "WATCHLIST / REJECT"
    if asymmetry_verdict == "CORE_ELIGIBLE" and not red_flags:
        return "CORE CANDIDATE"
    return "PILOT CANDIDATE"


def _run_debt_gate(
    debt_structure: DebtStructure,
    debt_context: Mapping[str, Any],
    financials: FinancialInputs,
    valuation: ValuationAssumptions,
) -> Dict[str, Any]:
    context = dict(debt_context)
    missing = {"current_price_usd", "current_date"} - set(context)
    if missing:
        raise ValueError(f"debt_context em falta: {', '.join(sorted(missing))}")

    return maturity_wall_check(
        debt_structure,
        current_price_usd=float(context["current_price_usd"]),
        current_date=str(context["current_date"]),
        cash_usd=float(context.get("cash_usd", financials.cash_usd)),
        annual_fcf_usd=float(context.get("annual_fcf_usd", 0.0)),
        forecast_years=int(context.get("forecast_years", valuation.forecast_years)),
    )


def _review_scenario_gates(
    *,
    scenario_results: Sequence[Mapping[str, Any]],
    base_scenario_name: str,
) -> tuple[list[str], list[str], list[str]]:
    hard_vetoes: list[str] = []
    red_flags: list[str] = []
    watch_items: list[str] = []

    for result in scenario_results:
        name = str(result["scenario"])
        is_base = name == base_scenario_name
        physical_status = result.get("physical_feasibility", {}).get("status")
        margin_status = result.get("margin_credibility", {}).get("status")
        revenue_quality = result.get("revenue_quality_check")

        if physical_status == "PHYSICALLY_UNSUPPORTED":
            if is_base:
                hard_vetoes.append("base_scenario_physically_unsupported")
            else:
                red_flags.append(f"{name}: physically unsupported")
        elif physical_status == "STRETCHED" and is_base:
            red_flags.append("base_scenario_physical_capacity_stretched")

        if margin_status == "IMPLAUSIBLE_MARGIN":
            if is_base:
                hard_vetoes.append("base_scenario_implausible_margin")
            else:
                red_flags.append(f"{name}: implausible margin")
        elif margin_status == "STRETCHED_MARGIN" and is_base:
            red_flags.append("base_scenario_margin_stretched")

        if result.get("tv_dominance_flag") == "TV_DOMINATED":
            watch_items.append(f"{name}: terminal value dominates scenario valuation")

        if revenue_quality and revenue_quality.get("verdict") == "NRE_DOMINATED_BACKLOG":
            red_flags.append(f"{name}: NRE-dominated backlog")

        concentration = result.get("physical_feasibility", {}).get("concentration_verdict")
        if concentration == "SEVERE_CONCENTRATION_RISK":
            red_flags.append(f"{name}: severe customer concentration risk")
        elif concentration == "ELEVATED_CONCENTRATION_RISK":
            watch_items.append(f"{name}: elevated customer concentration risk")

    return _unique(hard_vetoes), _unique(red_flags), _unique(watch_items)


def _review_debt_gate(debt_result: Mapping[str, Any]) -> tuple[list[str], list[str], list[str]]:
    """
    Traduz estados factuais de dívida em política de decisão.

    - Uncovered maturity wall: hard veto.
    - Partial coverage: red flag, porque exige refinanciamento mas não implica
      por si só insolvência inevitável.
    - Covered / beyond forecast horizon: sem flag de maturity wall.
    - Convertível in the money: red flag por pressão de diluição.
    - Convertível near the money: watch item.
    """
    hard_vetoes: list[str] = []
    red_flags: list[str] = []
    watch_items: list[str] = []

    instruments = debt_result.get("instruments", [])
    uncovered = [
        instrument for instrument in instruments
        if instrument.get("wall_status") == "MATURITY_WALL_UNCOVERED"
    ]
    partial = [
        instrument for instrument in instruments
        if instrument.get("wall_status") == "MATURITY_WALL_PARTIAL_COVERAGE"
    ]

    if uncovered:
        hard_vetoes.append("capital_structure_crisis_risk")
    if partial:
        red_flags.append("partial_maturity_wall_coverage")

    for instrument in instruments:
        overhang = instrument.get("overhang_status")
        if overhang == "IN_THE_MONEY_ACTIVE_DILUTION_PRESSURE":
            red_flags.append("convertible is in the money: active dilution pressure")
        elif overhang == "NEAR_THE_MONEY_WATCH":
            watch_items.append("convertible is near the money")

    return _unique(hard_vetoes), _unique(red_flags), _unique(watch_items)


def run_full_case(
    *,
    financials: FinancialInputs,
    factory: FactoryData,
    valuation: ValuationAssumptions,
    scenarios: Sequence[Scenario],
    config: Optional[EngineConfig] = None,
    revenue_quality_gates: Optional[Mapping[str, RevenueQualityGate]] = None,
    debt_structure: Optional[DebtStructure] = None,
    debt_context: Optional[Mapping[str, Any]] = None,
    portfolio: Optional[Mapping[str, float]] = None,
    portfolio_risk_engine: Optional[Any] = None,
    portfolio_confidence: float = 0.95,
    portfolio_impact_metric: str = "H_95",
) -> Dict[str, Any]:
    """Executa uma análise completa e devolve decisão, flags e resultados."""
    if not scenarios:
        raise ValueError("scenarios não pode ser vazio.")
    if debt_structure is not None and debt_context is None:
        raise ValueError("debt_context é obrigatório quando debt_structure é fornecido.")
    if portfolio is not None and portfolio_risk_engine is None:
        raise ValueError("portfolio_risk_engine é obrigatório quando portfolio é fornecido.")
    if not 0.0 < portfolio_confidence < 1.0:
        raise ValueError("portfolio_confidence deve estar entre 0 e 1.")
    if portfolio_impact_metric not in {"MI_total", "H_95"}:
        raise ValueError("portfolio_impact_metric deve ser 'MI_total' ou 'H_95'.")

    valuation_engine = ExpectationsGapEngine(
        financials=financials,
        factory=factory,
        valuation=valuation,
        scenarios=scenarios,
        config=config,
    )
    valuation_result = valuation_engine.run(
        revenue_quality_gates=dict(revenue_quality_gates or {})
    )

    scenario_results = valuation_result["scenario_valuation"]
    base_name = _base_scenario_name(scenarios)
    hard_vetoes, red_flags, watch_items = _review_scenario_gates(
        scenario_results=scenario_results,
        base_scenario_name=base_name,
    )
    red_flags.extend(valuation_result.get("red_flags", []))

    debt_result = None
    if debt_structure is not None:
        debt_result = _run_debt_gate(
            debt_structure=debt_structure,
            debt_context=debt_context or {},
            financials=financials,
            valuation=valuation,
        )
        debt_hard_vetoes, debt_red_flags, debt_watch_items = _review_debt_gate(debt_result)
        hard_vetoes.extend(debt_hard_vetoes)
        red_flags.extend(debt_red_flags)
        watch_items.extend(debt_watch_items)

    portfolio_result = None
    if portfolio is not None:
        portfolio_result = portfolio_risk_engine.liquidation_var(
            dict(portfolio),
            confidence=portfolio_confidence,
            impact_metric=portfolio_impact_metric,
        )
        extreme_tickers = [
            ticker
            for ticker, impact in portfolio_result.get("individual_impacts", {}).items()
            if float(impact.get("H_95", 0.0)) >= 0.25
        ]
        if extreme_tickers:
            red_flags.append("extreme liquidation haircut: " + ", ".join(sorted(extreme_tickers)))

    hard_vetoes = _unique(hard_vetoes)
    red_flags = _unique(red_flags)
    watch_items = _unique(watch_items)
    decision = _classify_decision(
        asymmetry_verdict=valuation_result["asymmetry"]["verdict"],
        hard_vetoes=hard_vetoes,
        red_flags=red_flags,
    )

    return {
        "runner_version": RUNNER_VERSION,
        "decision": decision,
        "base_scenario": base_name,
        "hard_vetoes": hard_vetoes,
        "red_flags": red_flags,
        "watch_items": watch_items,
        "valuation": valuation_result,
        "debt_structure": debt_result,
        "portfolio_risk": portfolio_result,
    }
