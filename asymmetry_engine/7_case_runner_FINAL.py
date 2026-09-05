"""
Case Runner / Decision Orchestrator — v1.0.0
==============================================

Camada de decisao agregada para o Asymmetry Engine.

Integra:
- ExpectationsGapEngine (valuation, fisica, margem, NOL, backlog);
- maturity_wall_check (estrutura de capital, opcional);
- AdvancedPortfolioRiskEngine (liquidez, opcional).

O objetivo e impedir que um resultado local favoravel, por exemplo
PILOT ou CORE pelo asymmetry ratio, esconda um hard veto de estrutura
de capital ou de liquidez.

Este ficheiro nao altera os tres motores existentes. E uma camada de
orquestracao independente e, portanto, pode ser removido sem alterar
os calculos internos de valuation, risco ou divida.
"""

from __future__ import annotations

from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Mapping, Optional, Sequence

from expectations_gap_engine_FINAL import (
    EngineConfig,
    ExpectationsGapEngine,
    FactoryData,
    FinancialInputs,
    RevenueQualityGate,
    Scenario,
    ValuationAssumptions,
)
from debt_structure_gate_FINAL import DebtStructure, maturity_wall_check


HARD_VETO_CODES = {
    "capital_structure_crisis_risk",
    "base_scenario_physically_unsupported",
    "base_scenario_implausible_margin",
}


def _scenario_lookup(scenario_results: Sequence[Mapping[str, Any]]) -> Dict[str, Mapping[str, Any]]:
    return {str(result["scenario"]): result for result in scenario_results}


def _base_scenario_name(scenarios: Sequence[Scenario]) -> str:
    """Escolhe primeiro um nome com 'base'; se nao existir, maior probabilidade."""
    base_named = [s for s in scenarios if "base" in s.name.lower()]
    if base_named:
        return max(base_named, key=lambda s: s.probability).name
    return max(scenarios, key=lambda s: s.probability).name


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
    """
    Corre o caso completo e devolve uma decisao auditavel.

    debt_context e obrigatorio quando debt_structure e fornecido. Deve
    conter current_price_usd e current_date; annual_fcf_usd e opcional
    (por defeito: 0). cash_usd e usado a partir de financials, salvo
    override explicito dentro de debt_context.

    portfolio_risk_engine deve expor liquidation_var(portfolio, ...).
    A camada de liquidez e opcional porque requer dados de mercado.
    """
    if not scenarios:
        raise ValueError("scenarios nao pode ser vazio.")
    if debt_structure is not None and debt_context is None:
        raise ValueError("debt_context e obrigatorio quando debt_structure e fornecido.")
    if portfolio is not None and portfolio_risk_engine is None:
        raise ValueError("portfolio_risk_engine e obrigatorio quando portfolio e fornecido.")

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
    scenario_by_name = _scenario_lookup(scenario_results)
    base_name = _base_scenario_name(scenarios)
    base_result = scenario_by_name[base_name]

    hard_vetoes = []
    red_flags = list(valuation_result.get("red_flags", []))
    watch_items = []

    base_physical_status = base_result["physical_feasibility"].get("status")
    if base_physical_status == "PHYSICALLY_UNSUPPORTED":
        hard_vetoes.append("base_scenario_physically_unsupported")
    elif base_physical_status == "STRETCHED":
        red_flags.append("base_scenario_physical_capacity_stretched")

    base_margin_status = base_result["margin_credibility"].get("status")
    if base_margin_status == "IMPLAUSIBLE_MARGIN":
        hard_vetoes.append("base_scenario_implausible_margin")
    elif base_margin_status == "STRETCHED_MARGIN":
        red_flags.append("base_scenario_margin_stretched")

    for result in scenario_results:
        scenario_name = result["scenario"]
        physical_status = result["physical_feasibility"].get("status")
        margin_status = result["margin_credibility"].get("status")
        if physical_status == "PHYSICALLY_UNSUPPORTED" and scenario_name != base_name:
            red_flags.append(f"{scenario_name}: physically unsupported")
        if margin_status == "IMPLAUSIBLE_MARGIN" and scenario_name != base_name:
            red_flags.append(f"{scenario_name}: implausible margin")
        if result.get("tv_dominance_flag") == "TV_DOMINATED":
            watch_items.append(f"{scenario_name}: terminal value dominates scenario valuation")
        quality = result.get("revenue_quality_check")
        if quality and quality.get("verdict") == "NRE_DOMINATED_BACKLOG":
            red_flags.append(f"{scenario_name}: NRE-dominated backlog")

    debt_result = None
    if debt_structure is not None:
        context = dict(debt_context or {})
        missing = {"current_price_usd", "current_date"} - set(context)
        if missing:
            raise ValueError(f"debt_context em falta: {sorted(missing)}")
        debt_result = maturity_wall_check(
            debt_structure,
            current_price_usd=float(context["current_price_usd"]),
            current_date=str(context["current_date"]),
            cash_usd=float(context.get("cash_usd", financials.cash_usd)),
            annual_fcf_usd=float(context.get("annual_fcf_usd", 0.0)),
            forecast_years=int(context.get("forecast_years", valuation.forecast_years)),
        )
        if debt_result["verdict"] == "CAPITAL_STRUCTURE_CRISIS_RISK":
            hard_vetoes.append("capital_structure_crisis_risk")
        else:
            for instrument in debt_result.get("instruments", []):
                if instrument.get("overhang_status") == "NEAR_THE_MONEY_WATCH":
                    watch_items.append("convertible is near the money")
                if instrument.get("overhang_status") == "IN_THE_MONEY_ACTIVE_DILUTION_PRESSURE":
                    red_flags.append("convertible is in the money: active dilution pressure")

    portfolio_result = None
    if portfolio is not None:
        portfolio_result = portfolio_risk_engine.liquidation_var(
            dict(portfolio),
            confidence=portfolio_confidence,
            impact_metric=portfolio_impact_metric,
        )
        individual_impacts = portfolio_result.get("individual_impacts", {})
        extreme = [ticker for ticker, impact in individual_impacts.items() if impact.get("H_95", 0.0) >= 0.25]
        if extreme:
            red_flags.append("extreme liquidation haircut: " + ", ".join(sorted(extreme)))

    # Remove duplicados sem perder ordem, para tornar output deterministico.
    hard_vetoes = list(dict.fromkeys(hard_vetoes))
    red_flags = list(dict.fromkeys(red_flags))
    watch_items = list(dict.fromkeys(watch_items))

    decision = _classify_decision(
        asymmetry_verdict=valuation_result["asymmetry"]["verdict"],
        hard_vetoes=hard_vetoes,
        red_flags=red_flags,
    )

    return {
        "runner_version": "CASE-RUNNER-1.0.0",
        "decision": decision,
        "base_scenario": base_name,
        "hard_vetoes": hard_vetoes,
        "red_flags": red_flags,
        "watch_items": watch_items,
        "valuation": valuation_result,
        "debt_structure": debt_result,
        "portfolio_risk": portfolio_result,
    }


if __name__ == "__main__":
    # Exemplo mínimo sem dados de mercado nem estrutura de dívida.
    financials = FinancialInputs(
        market_cap_usd=500_000_000,
        total_debt_usd=50_000_000,
        cash_usd=40_000_000,
        current_revenue_usd=100_000_000,
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
        business_model="fab",
    )
    valuation = ValuationAssumptions(
        wacc=0.12,
        wacc_initial=0.20,
        wacc_terminal=0.10,
        forecast_years=5,
        tax_rate=0.21,
    )
    scenarios = [
        Scenario("Bear", 0.25, -0.10, 0.05, 0.40, 8.0),
        Scenario("Base", 0.50, 0.20, 0.15, 0.30, 12.0),
        Scenario("Bull", 0.25, 0.40, 0.25, 0.20, 18.0),
    ]
    result = run_full_case(
        financials=financials,
        factory=factory,
        valuation=valuation,
        scenarios=scenarios,
    )
    print(result["decision"])
    print(result["hard_vetoes"])
