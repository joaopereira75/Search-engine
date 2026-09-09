"""
ghost_parameter_audit.py

Para cada campo de cada dataclass do motor:
1. Cria uma configuração base.
2. Cria uma cópia com ESSE campo mudado para um valor extremo/diferente.
3. Corre o motor completo (run_full_case) em ambas.
4. Compara o JSON serializado inteiro.
5. Se for byte-a-byte idêntico -> candidato a "ghost parameter".

Corre isto em 3 configurações base diferentes (fab, not_applicable,
com debt_structure) para reduzir falsos positivos de campos que só têm
efeito sob certas condições (ex: variable_cost_per_unit só importa com
asp_usd > 0).
"""
import copy
import json
import sys
from dataclasses import fields, replace

sys.path.insert(0, "/home/claude")
from asymmetry_engine.expectations_gap_engine import (
    FactoryData, FinancialInputs, ValuationAssumptions, Scenario, EngineConfig,
    BacklogItem, RevenueQualityGate,
)
from asymmetry_engine.debt_structure_gate import ConvertibleInstrument, DebtStructure
from asymmetry_engine.case_runner import run_full_case


def canon(obj):
    """Serializa de forma determinística, tratando NaN/Inf."""
    def default(o):
        if hasattr(o, "item"):
            return o.item()
        return str(o)
    return json.dumps(obj, sort_keys=True, default=default)


def run_case(financials, factory, valuation, scenarios, config=None, debt_structure=None, debt_context=None):
    return run_full_case(
        financials=financials, factory=factory, valuation=valuation,
        scenarios=scenarios, config=config,
        debt_structure=debt_structure, debt_context=debt_context,
    )


PERTURBATIONS = {
    # tipo de campo -> valor alternativo a testar (deve ser "razoavelmente diferente")
    float: lambda cur: (cur * 3.7 + 1.0) if cur is not None else 7.0,
    int: lambda cur: (cur + 3) if cur is not None else 4,
    bool: lambda cur: (not cur) if cur is not None else True,
    str: lambda cur: cur,  # tratado à parte (enums)
}

ENUM_ALTERNATIVES = {
    "business_model": ["fab", "equipment_vendor", "not_applicable"],
}

BOUNDED_FRACTION_FIELDS = {
    "current_utilization", "yield_rate", "top_customer_revenue_pct",
    "tax_rate", "terminal_growth", "target_ebit_margin", "reinvestment_rate",
    "probability", "ebit_margin", "dilution_warning_pct", "dilution_reject_pct",
    "nol_utilization_cap_pct", "terminal_value_dominance_warning_pct",
    "margin_implausibility_threshold_pp", "physical_feasibility_tolerance",
    "severe_implied_revenue_gap", "min_incremental_roic_for_value_creation",
    "strong_incremental_roic_spread", "core_asymmetry_threshold",
    "pilot_asymmetry_threshold", "partial_coverage_max_gap_pct",
    "coupon_rate",
}


def alt_value(field_name, current):
    if field_name in ENUM_ALTERNATIVES:
        options = ENUM_ALTERNATIVES[field_name]
        return next(o for o in options if o != current)
    if current is None:
        return None  # não perturbamos campos None (Optional não usado no baseline)
    if isinstance(current, bool):
        return not current
    if isinstance(current, str):
        return current + "_ALT"
    if isinstance(current, (int, float)):
        if field_name in BOUNDED_FRACTION_FIELDS:
            # mover para o outro lado do intervalo razoável, mantendo válido
            if current < 0.3:
                return min(0.95, current + 0.4)
            return max(0.01, current - 0.4)
        if field_name == "exit_multiple":
            return current * 2.5
        return current * 3.3 + 1.0
    return current


def perturb_dataclass_fields(instance, run_fn, base_output, label, results, scenario_index=None):
    for f in fields(instance):
        current = getattr(instance, f.name)
        new_value = alt_value(f.name, current)
        if new_value == current or new_value is None:
            results.setdefault(label, {})[f.name] = "SKIPPED (sem valor alternativo válido)"
            continue
        try:
            modified = replace(instance, **{f.name: new_value})
        except Exception as e:
            results.setdefault(label, {})[f.name] = f"SKIPPED (replace falhou: {e})"
            continue
        try:
            new_output = run_fn(modified)
        except Exception as e:
            results.setdefault(label, {})[f.name] = f"SKIPPED (execução falhou com valor alterado: {type(e).__name__})"
            continue
        same = canon(new_output) == canon(base_output)
        results.setdefault(label, {})[f.name] = (
            f"*** SEM EFEITO (valor testado: {current} -> {new_value}) ***" if same
            else f"OK (efeito detetado; {current} -> {new_value})"
        )


def main():
    results = {}

    # ---- Configuração base 1: fab, sem dívida ----
    financials = FinancialInputs(
        market_cap_usd=500_000_000, total_debt_usd=50_000_000, cash_usd=40_000_000,
        current_revenue_usd=100_000_000, current_shares=120_000_000, nol_balance_usd=85_000_000,
        current_ebitda_usd=10_000_000, current_invested_capital_usd=200_000_000,
        share_price_usd=4.17,
    )
    factory = FactoryData(
        capacity_max_units=2_000_000, current_capacity_units=1_200_000, current_utilization=0.80,
        yield_rate=0.90, asp_usd=250.0, expansion_capacity_units=800_000,
        expansion_lead_time_years=1.0, qualification_lead_time_years=0.5, ramp_years=1.0,
        variable_cost_per_unit=150.0, maintenance_capex_per_unit=5.0, incremental_capex_usd=1_000_000,
        business_model="fab", top_customer_revenue_pct=0.20,
    )
    valuation = ValuationAssumptions(
        wacc=0.12, wacc_initial=0.20, wacc_terminal=0.10, wacc_glide_years=5,
        forecast_years=5, tax_rate=0.21, terminal_growth=0.02, target_ebit_margin=0.20,
        revenue_to_invested_capital=1.5, reinvestment_rate=0.25,
    )
    config = EngineConfig()
    scenario_base = Scenario(
        name="Base", probability=1.0, revenue_cagr=0.20, ebit_margin=0.15, reinvestment_rate=0.30,
        exit_multiple=12.0, incremental_capex_usd=2_000_000, additional_debt_usd=5_000_000,
        dilution_usd=30_000_000,
    )
    scenarios = [scenario_base]

    def run_with(financials=financials, factory=factory, valuation=valuation, scenarios=scenarios, config=config):
        return run_case(financials, factory, valuation, scenarios, config=config)

    base_output = run_with()

    perturb_dataclass_fields(
        financials, lambda x: run_with(financials=x), base_output, "FinancialInputs", results
    )
    perturb_dataclass_fields(
        factory, lambda x: run_with(factory=x), base_output, "FactoryData", results
    )
    perturb_dataclass_fields(
        valuation, lambda x: run_with(valuation=x), base_output, "ValuationAssumptions", results
    )
    perturb_dataclass_fields(
        config, lambda x: run_with(config=x), base_output, "EngineConfig", results
    )
    perturb_dataclass_fields(
        scenario_base, lambda x: run_with(scenarios=[x]), base_output, "Scenario", results
    )

    # ---- Configuração 2: debt_structure, para testar straight_debt_usd etc ----
    debt = DebtStructure(
        instruments=[ConvertibleInstrument(principal_usd=100_000_000, conversion_price_usd=50.0,
                                            maturity_date="2027-01-01", coupon_rate=0.02)],
        straight_debt_usd=200_000_000,
        partial_coverage_max_gap_pct=0.50,
    )
    debt_context = {"current_price_usd": 40.0, "current_date": "2024-01-01", "annual_fcf_usd": -10_000_000}

    def run_with_debt(debt=debt):
        return run_case(financials, factory, valuation, scenarios, config=config,
                         debt_structure=debt, debt_context=debt_context)

    base_debt_output = run_with_debt()
    perturb_dataclass_fields(debt, run_with_debt, base_debt_output, "DebtStructure", results)

    instrument = debt.instruments[0]

    def run_with_instrument(instrument=instrument):
        new_debt = replace(debt, instruments=[instrument])
        return run_with_debt(new_debt)

    perturb_dataclass_fields(instrument, run_with_instrument, base_debt_output, "ConvertibleInstrument", results)

    # ---- Configuração 3: RevenueQualityGate ----
    gate = RevenueQualityGate(items=[BacklogItem("x", 10_000_000, "direct_po")])

    def run_with_gate(financials=financials, factory=factory, valuation=valuation, scenarios=scenarios, config=config):
        eng_scenarios = scenarios
        return run_full_case(
            financials=financials, factory=factory, valuation=valuation,
            scenarios=eng_scenarios, config=config,
            revenue_quality_gates={"Base": gate},
        )

    base_gate_output = run_with_gate()
    item = gate.items[0]

    def run_with_item(item=item):
        new_gate = RevenueQualityGate(items=[item])
        nonlocal gate
        gate_backup = gate
        gate = new_gate
        try:
            return run_with_gate()
        finally:
            gate = gate_backup

    perturb_dataclass_fields(item, run_with_item, base_gate_output, "BacklogItem", results)

    # ---- Print report ----
    print("=" * 100)
    print("GHOST PARAMETER AUDIT — resultado por campo")
    print("=" * 100)
    dead_fields = []
    for group, fields_result in results.items():
        print(f"\n### {group}")
        for field_name, verdict in fields_result.items():
            print(f"  {field_name:35s} {verdict}")
            if "SEM EFEITO" in verdict:
                dead_fields.append(f"{group}.{field_name}")

    print("\n" + "=" * 100)
    print(f"RESUMO: {len(dead_fields)} campo(s) sem qualquer efeito detetado no output completo:")
    for d in dead_fields:
        print(f"  - {d}")
    print("=" * 100)


if __name__ == "__main__":
    main()
