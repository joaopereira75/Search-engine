"""
asymmetry_engine/reporting.py

Relatórios Markdown auditáveis para o Asymmetry Engine.

Aceita dois tipos de payload:

1. Resultado normal de ``run_case_config()`` / ``run_full_case()``.
2. Research package de ``run_research_pipeline()``.

No modo research, o relatório acrescenta validation, evidence coverage,
ranking de drivers, sensitivities one-way, break-even conditions e erros
isolados de research. O relatório usa tabelas estreitas de duas colunas
para permanecer legível no preview do Positron/VS Code.
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence


REPORTING_VERSION = "REPORTING-4.3.0"


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        if hasattr(value, "item"):
            value = value.item()
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _text(value: Any, default: str = "n/a") -> str:
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def _money(value: Any, currency: str = "USD", decimals: int = 0) -> str:
    number = _number(value)
    return "n/a" if number is None else f"{currency} {number:,.{decimals}f}"


def _pct(value: Any, decimals: int = 1) -> str:
    number = _number(value)
    return "n/a" if number is None else f"{number:.{decimals}%}"


def _multiple(value: Any, decimals: int = 2) -> str:
    number = _number(value)
    return "n/a" if number is None else f"{number:.{decimals}f}x"


def _plain(value: Any, decimals: int = 2) -> str:
    number = _number(value)
    return "n/a" if number is None else f"{number:,.{decimals}f}"


def _driver_value(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return str(value).lower()
    number = _number(value)
    if number is not None:
        return f"{number:,.6g}"
    return _text(value)


def _cell(value: Any) -> str:
    return _text(value).replace("|", "\\|").replace("\n", " ")


def _key_value_table(rows: Sequence[tuple[str, Any]]) -> list[str]:
    lines = ["| Metric | Value |", "| --- | --- |"]
    lines.extend(f"| {_cell(key)} | {_cell(value)} |" for key, value in rows)
    return lines


def _list_section(title: str, items: Sequence[Any], empty: str = "None.") -> list[str]:
    lines = [f"## {title}", ""]
    if items:
        lines.extend(f"- {_text(item)}" for item in items)
    else:
        lines.append(f"- {empty}")
    return [*lines, ""]


def _status_emoji(status: Any) -> str:
    status = _text(status, "").upper()
    if any(token in status for token in ("UNSUPPORTED", "CRISIS", "UNCOVERED", "REJECT", "FAIL", "HARD_FAIL")):
        return "🔴"
    if any(token in status for token in ("WATCH", "DOMINATED", "OVERHANG", "WARNING", "PARTIAL", "LOW")):
        return "🟡"
    if any(token in status for token in ("FEASIBLE", "CONSISTENT", "OK", "PASS", "COVERED", "HIGH")):
        return "🟢"
    return "⚪"


def _status(value: Any) -> str:
    label = _text(value)
    return f"{_status_emoji(label)} {label}"


def _is_research_package(payload: Mapping[str, Any]) -> bool:
    return "base_case" in payload and "research_pipeline_version" in payload


def _case_header(result: Mapping[str, Any], research: bool) -> list[str]:
    case = result.get("case", {})
    company = _text(case.get("company_name"), "Unnamed case")
    ticker = _text(case.get("ticker"), "N/A")
    title = "Research package" if research else "Investment case"
    return [
        f"# {title} — {company} ({ticker})",
        "",
        f"> **Point in time:** {_text(case.get('point_in_time'))}  ",
        f"> **Currency:** {_text(case.get('currency'), 'USD')}  ",
        f"> **Case type:** {_text(case.get('case_type'))}",
        "",
    ]


def _validation_section(validation: Mapping[str, Any]) -> list[str]:
    if not validation:
        return []
    coverage = validation.get("input_coverage", {})
    lines = ["## Validation and evidence", ""]
    lines.extend(_key_value_table([
        ("Validation status", _status(validation.get("status"))),
        ("Evidence coverage", f"{_plain(coverage.get('coverage_pct'), decimals=1)}%"),
        ("Material inputs tracked", _plain(coverage.get("material_fields_required"), decimals=0)),
        ("Inputs with source", _plain(coverage.get("material_fields_with_source"), decimals=0)),
        ("Inputs with as-of date", _plain(coverage.get("material_fields_with_as_of"), decimals=0)),
        ("Inputs with confidence", _plain(coverage.get("material_fields_with_confidence"), decimals=0)),
    ]))
    lines.append("")

    warnings = list(validation.get("economic_warnings", []))
    if warnings:
        lines.append("### Economic warnings")
        lines.append("")
        lines.extend(f"- {_text(warning)}" for warning in warnings)
        lines.append("")
    return lines


def _executive_summary(result: Mapping[str, Any]) -> list[str]:
    case = result.get("case", {})
    valuation = result.get("valuation", {})
    asymmetry = valuation.get("asymmetry", {})
    weighted = valuation.get("probability_weighted_valuation", {})
    currency = _text(case.get("currency"), "USD")

    lines = ["## Executive summary", ""]
    lines.extend(_key_value_table([
        ("Decision", _status(result.get("decision"))),
        ("Base scenario", _text(result.get("base_scenario"))),
        ("Asymmetry ratio", _multiple(asymmetry.get("asymmetry_ratio"))),
        ("Asymmetry classification", _status(asymmetry.get("verdict"))),
        ("Probability-weighted equity value", _money(weighted.get("expected_equity_value_usd"), currency)),
        ("Probability-weighted return", _pct(weighted.get("expected_return"))),
    ]))
    lines.append("")

    if result.get("hard_vetoes"):
        lines.append("**Investment conclusion:** Do not approve while hard vetoes remain unresolved.")
    elif result.get("red_flags"):
        lines.append("**Investment conclusion:** Requires further diligence before approval.")
    else:
        lines.append("**Investment conclusion:** No hard vetoes identified by the supplied model inputs.")
    return [*lines, ""]


def _context_section(result: Mapping[str, Any]) -> list[str]:
    notes = result.get("case", {}).get("notes")
    if not notes:
        return []
    return ["## Case context", "", _text(notes), ""]


def _scenario_section(result: Mapping[str, Any]) -> list[str]:
    currency = _text(result.get("case", {}).get("currency"), "USD")
    scenarios = result.get("valuation", {}).get("scenario_valuation", [])
    lines = ["## Scenario valuation", ""]
    if not scenarios:
        return [*lines, "No scenario valuation results were supplied.", ""]

    for scenario in scenarios:
        name = _text(scenario.get("scenario"), "Unnamed scenario")
        revenue = _number(scenario.get("revenue_year_n_usd"))
        ebit = _number(scenario.get("ebit_year_n_usd"))
        margin = None if revenue in (None, 0) or ebit is None else ebit / revenue
        physical = scenario.get("physical_feasibility", {})
        margin_gate = scenario.get("margin_credibility", {})
        quality = scenario.get("revenue_quality_check") or {}
        lines.extend([
            f"### {name}",
            "",
            *(_key_value_table([
                ("Probability", _pct(scenario.get("probability"), decimals=0)),
                ("Revenue at year N", _money(revenue, currency)),
                ("EBIT at year N", _money(ebit, currency)),
                ("EBIT margin", _pct(margin)),
                ("Enterprise value", _money(scenario.get("enterprise_value_usd"), currency)),
                ("Equity value", _money(scenario.get("equity_value_usd"), currency)),
                ("Return", _pct(scenario.get("return_pct"))),
                ("Physical feasibility", _status(physical.get("status"))),
                ("Margin credibility", _status(margin_gate.get("status"))),
                ("Terminal value", _status(scenario.get("tv_dominance_flag"))),
                ("Backlog quality", _status(quality.get("verdict")) if quality else "Not assessed"),
            ])),
            "",
        ])
    return lines


def _operating_gates_section(result: Mapping[str, Any]) -> list[str]:
    currency = _text(result.get("case", {}).get("currency"), "USD")
    scenarios = result.get("valuation", {}).get("scenario_valuation", [])
    lines = ["## Operating gates", ""]
    if not scenarios:
        return [*lines, "No operating-gate results were supplied.", ""]

    for scenario in scenarios:
        name = _text(scenario.get("scenario"), "Unnamed scenario")
        physical = scenario.get("physical_feasibility", {})
        margin = scenario.get("margin_credibility", {})
        backlog = scenario.get("revenue_quality_check") or {}
        concentration = physical.get("concentration_verdict")
        physical_label = physical.get("status")
        if concentration:
            physical_label = f"{_text(physical_label)}; {_text(concentration)}"
        lines.extend([
            f"### {name}",
            "",
            *(_key_value_table([
                ("Physical / concentration", _status(physical_label)),
                ("Physical feasibility gap", _pct(physical.get("physical_feasibility_gap"))),
                ("Margin gate", _status(margin.get("status"))),
                ("Margin gap", _pct(margin.get("margin_gap_pp"))),
                ("Backlog quality", _status(backlog.get("verdict")) if backlog else "Not assessed"),
                ("Effective backlog", _money(backlog.get("effective_backlog_usd"), currency) if backlog else "n/a"),
            ])),
            "",
        ])
    return lines


def _debt_section(result: Mapping[str, Any]) -> list[str]:
    debt = result.get("debt_structure")
    currency = _text(result.get("case", {}).get("currency"), "USD")
    lines = ["## Capital structure and maturity wall", ""]
    if debt is None:
        return [*lines, "No debt structure was supplied for this case.", ""]

    lines.extend(_key_value_table([
        ("Debt verdict", _status(debt.get("verdict"))),
        ("Total refinancing gap", _money(debt.get("total_refinancing_gap_usd"), currency)),
        ("Largest gap as percentage of principal", _pct(debt.get("max_gap_pct_of_principal"))),
        ("Uncovered instruments", _plain(debt.get("uncovered_instrument_count"), decimals=0)),
        ("Partial-coverage instruments", _plain(debt.get("partial_coverage_instrument_count"), decimals=0)),
    ]))
    lines.append("")

    for index, instrument in enumerate(debt.get("instruments", []), start=1):
        lines.extend([
            f"### Debt instrument {index}",
            "",
            *(_key_value_table([
                ("Principal", _money(instrument.get("principal_usd"), currency)),
                ("Maturity date", _text(instrument.get("maturity_date"))),
                ("Years to maturity", _plain(instrument.get("years_to_maturity"))),
                ("Price / conversion price", _multiple(instrument.get("price_to_conversion_ratio"))),
                ("Conversion overhang", _status(instrument.get("overhang_status"))),
                ("Maturity wall", _status(instrument.get("wall_status"))),
                ("Projected cash available", _money(instrument.get("projected_cash_available_usd"), currency)),
                ("Refinancing gap", _money(instrument.get("refinancing_gap_usd"), currency)),
                ("Gap / principal", _pct(instrument.get("gap_pct_of_principal"))),
                ("Partial coverage threshold", _pct(instrument.get("partial_coverage_max_gap_pct"))),
            ])),
            "",
        ])
    return lines


def _liquidity_section(result: Mapping[str, Any]) -> list[str]:
    risk = result.get("portfolio_risk")
    currency = _text(result.get("case", {}).get("currency"), "USD")
    lines = ["## Portfolio liquidity risk", ""]
    if risk is None:
        return [*lines, "No portfolio liquidity analysis was requested for this case.", ""]

    lines.extend(_key_value_table([
        ("Gross exposure", _money(risk.get("gross_value_usd"), currency)),
        ("Impact metric used", _text(risk.get("impact_metric_used"))),
        ("Liquidation VaR", _money(risk.get("liquidation_var_usd"), currency)),
        ("Expected shortfall", _money(risk.get("expected_shortfall_usd"), currency)),
    ]))
    lines.append("")
    horizons = risk.get("liquidity_horizon_by_ticker", {})
    impacts = risk.get("individual_impacts", {})
    for ticker in sorted(set(horizons) | set(impacts)):
        impact = impacts.get(ticker, {})
        lines.extend([
            f"### {_text(ticker)}",
            "",
            *(_key_value_table([
                ("Liquidity horizon", f"{_plain(horizons.get(ticker), decimals=1)} days"),
                ("Market impact", _pct(impact.get("MI_total"))),
                ("H_95", _pct(impact.get("H_95"))),
                ("Participation", _plain(impact.get("phi"), decimals=3)),
            ])),
            "",
        ])
    return lines


def _input_value(field: str, value: Any, currency: str) -> str:
    if not isinstance(value, (int, float)):
        return _text(value)
    lowered = field.lower()
    if any(token in lowered for token in ("pct", "margin", "rate", "yield", "probability")):
        return _pct(value)
    if any(token in lowered for token in ("shares", "count", "units")):
        return _plain(value, decimals=0)
    return _money(value, currency)


def _inputs_section(result: Mapping[str, Any]) -> list[str]:
    sources = result.get("sources", [])
    currency = _text(result.get("case", {}).get("currency"), "USD")
    lines = ["## Inputs, sources and confidence", ""]
    if not sources:
        return [*lines, "No structured source metadata was supplied in the case configuration.", ""]

    for source in sources:
        if not isinstance(source, Mapping):
            continue
        field = _text(source.get("field"), "Unnamed input")
        lines.extend([
            f"### {field}",
            "",
            *(_key_value_table([
                ("Value", _input_value(field, source.get("value"), currency)),
                ("Confidence", _status(source.get("confidence"))),
                ("As of", _text(source.get("as_of"))),
                ("Source / method", _text(source.get("source"))),
                ("Unit", _text(source.get("unit"))),
                ("Currency", _text(source.get("currency"), currency)),
            ])),
            "",
        ])
    return lines


def _research_ranking_section(package: Mapping[str, Any]) -> list[str]:
    ranking = package.get("driver_ranking", [])
    lines = ["## Top decision drivers", ""]
    if not ranking:
        return [*lines, "No sensitivity driver ranking was produced.", ""]

    for item in ranking:
        label = _text(item.get("label", item.get("driver")), "Unnamed driver")
        lines.extend([
            f"### {_plain(item.get('rank'), decimals=0)}. {label}",
            "",
            *(_key_value_table([
                ("Driver", _text(item.get("driver"))),
                ("Materiality", _status(item.get("materiality"))),
                ("Impact score", _plain(item.get("impact_score"))),
                ("Decision flips", _plain(item.get("decision_flip_count"), decimals=0)),
                ("Hard-veto flips", _plain(item.get("hard_veto_flip_count"), decimals=0)),
                ("Expected-return range", _pct(item.get("expected_return_range"))),
                ("Asymmetry-ratio range", _plain(item.get("asymmetry_range"))),
                ("Refinancing-gap range", _money(item.get("refinancing_gap_range_usd"), _text(package.get("case", {}).get("currency"), "USD"))),
            ])),
            "",
        ])
    return lines


def _research_sensitivities_section(package: Mapping[str, Any]) -> list[str]:
    sensitivities = package.get("sensitivities", [])
    currency = _text(package.get("case", {}).get("currency"), "USD")
    lines = ["## One-way sensitivities", ""]
    if not sensitivities:
        return [*lines, "No sensitivity results were produced.", ""]

    for sensitivity in sensitivities:
        driver = _text(sensitivity.get("driver"), "Unnamed driver")
        baseline = sensitivity.get("baseline", {})
        lines.extend([
            f"### {driver}",
            "",
            *(_key_value_table([
                ("Baseline driver value", _driver_value(sensitivity.get("baseline_driver_value"))),
                ("Baseline decision", _status(baseline.get("decision"))),
                ("Baseline expected return", _pct(baseline.get("expected_return"))),
                ("Baseline refinancing gap", _money(baseline.get("refinancing_gap_usd"), currency)),
                ("Baseline hard vetoes", ", ".join(str(item) for item in baseline.get("hard_vetoes", [])) or "None"),
            ])),
            "",
        ])

        for point in sensitivity.get("points", []):
            lines.extend([
                f"#### Value: {_driver_value(point.get('value'))}",
                "",
                *(_key_value_table([
                    ("Decision", _status(point.get("decision"))),
                    ("Expected equity value", _money(point.get("expected_equity_value_usd"), currency)),
                    ("Expected return", _pct(point.get("expected_return"))),
                    ("Asymmetry ratio", _multiple(point.get("asymmetry_ratio"))),
                    ("Hard vetoes", ", ".join(str(item) for item in point.get("hard_vetoes", [])) or "None"),
                    ("Refinancing gap", _money(point.get("refinancing_gap_usd"), currency)),
                    ("Maximum physical gap", _pct(point.get("physical_feasibility_gap"))),
                    ("Dominant scenario", _text(point.get("dominant_scenario"))),
                    ("Decision or veto flip", "Yes" if point.get("decision_flip") else "No"),
                    ("Impact score", _plain(point.get("impact_score"))),
                ])),
                "",
            ])
    return lines


def _research_breakeven_section(package: Mapping[str, Any]) -> list[str]:
    solutions = package.get("breakevens", [])
    currency = _text(package.get("case", {}).get("currency"), "USD")
    lines = ["## Break-even conditions", ""]
    if not solutions:
        return [*lines, "No break-even results were produced.", ""]

    for solution in solutions:
        label = _text(solution.get("label"), "Break-even")
        threshold = solution.get("threshold") or {}
        lines.extend([
            f"### {label}",
            "",
            *(_key_value_table([
                ("Status", _status(solution.get("status"))),
                ("Driver", _text(solution.get("driver"))),
                ("Original value", _driver_value(solution.get("original_driver_value"))),
                ("Threshold value", _driver_value(solution.get("threshold_value"))),
                ("Resolution", _driver_value(solution.get("resolution"))),
                ("Iterations", _plain(solution.get("iterations"), decimals=0)),
                ("Threshold decision", _status(threshold.get("decision"))),
                ("Threshold hard vetoes", ", ".join(str(item) for item in threshold.get("hard_vetoes", [])) or "None"),
                ("Threshold refinancing gap", _money(threshold.get("refinancing_gap_usd"), currency)),
                ("Threshold expected return", _pct(threshold.get("expected_return"))),
                ("Threshold asymmetry", _multiple(threshold.get("asymmetry_ratio"))),
            ])),
            "",
        ])
    return lines


def _research_errors_section(package: Mapping[str, Any]) -> list[str]:
    errors = package.get("research_errors", [])
    if not errors:
        return []
    lines = ["## Research items requiring review", ""]
    for error in errors:
        if isinstance(error, Mapping):
            lines.append(
                f"- {_text(error.get('stage'), 'research')}[{_text(error.get('item'), '?')}]: "
                f"{_text(error.get('error'), 'unknown error')}"
            )
        else:
            lines.append(f"- {_text(error)}")
    return [*lines, ""]


def _limitations_section(result: Mapping[str, Any]) -> list[str]:
    limitations = [
        "Thresholds are heuristic and have not been statistically calibrated.",
        "Scenario probabilities, growth, margins, reinvestment, dilution and exit multiples are analyst assumptions unless explicitly sourced.",
        "The model is a research and scenario-structuring tool, not investment advice or a price target.",
        "Break-even values are conditional on all non-varied inputs remaining unchanged and on the monotonicity assumption for the selected driver.",
    ]
    low_confidence = []
    for source in result.get("sources", []):
        if isinstance(source, Mapping) and _text(source.get("confidence"), "").lower() == "low":
            if source.get("field"):
                low_confidence.append(_text(source["field"]))
    if low_confidence:
        limitations.append("Low-confidence inputs requiring verification: " + ", ".join(dict.fromkeys(low_confidence)) + ".")
    return _list_section("Limitations and outstanding diligence", limitations)


def render_markdown_report(payload: Mapping[str, Any]) -> str:
    """Renderiza um resultado normal ou research package num relatório Markdown."""
    if not isinstance(payload, Mapping):
        raise TypeError("payload must be a mapping/result dictionary.")

    research = _is_research_package(payload)
    result = payload.get("base_case", {}) if research else payload
    if not isinstance(result, Mapping):
        raise TypeError("research package base_case must be a mapping/result dictionary.")

    # O research package guarda case/validation no topo; o resultado-base é a
    # fonte para valuation, flags, dívida, fontes e limitações.
    if research:
        result = dict(result)
        result["case"] = dict(payload.get("case", result.get("case", {})))
        result["validation"] = dict(payload.get("validation", result.get("validation", {})))

    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines: list[str] = []
    lines.extend(_case_header(result, research))
    lines.extend([f"_Generated by Asymmetry Engine {REPORTING_VERSION} on {generated_at}._", ""])
    lines.extend(_executive_summary(result))
    lines.extend(_validation_section(result.get("validation", {})))
    lines.extend(_context_section(result))
    lines.extend(_list_section("Hard vetoes", list(result.get("hard_vetoes", []))))
    lines.extend(_list_section("Red flags", list(result.get("red_flags", []))))
    lines.extend(_list_section("Watch items", list(result.get("watch_items", []))))

    if research:
        lines.extend(_research_ranking_section(payload))
        lines.extend(_research_breakeven_section(payload))
        lines.extend(_research_sensitivities_section(payload))
        lines.extend(_research_errors_section(payload))

    lines.extend(_scenario_section(result))
    lines.extend(_operating_gates_section(result))
    lines.extend(_debt_section(result))
    lines.extend(_liquidity_section(result))
    lines.extend(_inputs_section(result))
    lines.extend(_limitations_section(result))
    return "\n".join(lines).rstrip() + "\n"


def write_markdown_report(payload: Mapping[str, Any], output_path: str | Path) -> Path:
    """Escreve relatório Markdown e cria as pastas-pai necessárias."""
    path = Path(output_path)
    if path.suffix.lower() not in {".md", ".markdown"}:
        raise ValueError("output_path must end in .md or .markdown.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_report(payload), encoding="utf-8")
    return path
