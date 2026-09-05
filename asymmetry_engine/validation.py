"""
asymmetry_engine/validation.py

Validation v1 para configurações do Asymmetry Engine.

Valida dados antes da criação das dataclasses e antes de qualquer valuation.
Erros de schema ou consistência financeira dão HARD_FAIL; warnings económicos
não bloqueiam a análise, mas tornam evidência e discrepâncias auditáveis.

Campos opcionais do schema atual podem ser omitidos ou definidos como null no
YAML. Se forem fornecidos com um valor não nulo, têm de respeitar as regras
numéricas e económicas aplicáveis.
"""

from __future__ import annotations

from datetime import date
import math
from typing import Any, Mapping, Sequence


VALIDATION_VERSION = "VALIDATION-1.0.2"

HARD_FAIL = "HARD_FAIL"
PASS = "PASS"

MATERIAL_INPUT_FIELDS = frozenset({
    "share_price_usd",
    "current_shares",
    "market_cap_usd",
    "cash_usd",
    "total_debt_usd",
    "current_revenue_usd",
    "annual_fcf_usd",
    "factory_proxy",
    "debt_structure",
    "scenario_assumptions",
})

VALID_CONFIDENCE = frozenset({"high", "medium", "low"})


def _is_mapping(value: Any) -> bool:
    return isinstance(value, Mapping)


def _is_sequence(value: Any) -> bool:
    return isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))


def _as_number(value: Any) -> float | None:
    """Converte apenas números finitos; bool e null não são números."""
    if isinstance(value, bool) or value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _parse_iso_date(value: Any) -> date | None:
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _normalise_field_name(value: Any) -> str:
    return str(value).strip().lower()


def _append_unique(items: list[str], message: str) -> None:
    if message not in items:
        items.append(message)


def _validate_required_mapping(
    raw: Mapping[str, Any],
    field: str,
    errors: list[str],
) -> Mapping[str, Any]:
    value = raw.get(field)
    if not _is_mapping(value):
        _append_unique(errors, f"'{field}' must be a mapping/object.")
        return {}
    return value


def _validate_non_negative(
    mapping: Mapping[str, Any],
    field: str,
    errors: list[str],
    *,
    required: bool = False,
    strictly_positive: bool = False,
) -> None:
    """Valida campo numérico; null é aceite para campos opcionais."""
    if field not in mapping or mapping.get(field) is None:
        if required:
            _append_unique(errors, f"'{field}' is required and must be a finite number.")
        return
    number = _as_number(mapping.get(field))
    if number is None:
        _append_unique(errors, f"'{field}' must be a finite number.")
        return
    if strictly_positive and number <= 0:
        _append_unique(errors, f"'{field}' must be greater than zero.")
    elif not strictly_positive and number < 0:
        _append_unique(errors, f"'{field}' must not be negative.")


def _validate_fraction(
    mapping: Mapping[str, Any],
    field: str,
    errors: list[str],
    *,
    required: bool = False,
    allow_negative: bool = False,
) -> None:
    """Valida proporção; null é aceite para campos opcionais."""
    if field not in mapping or mapping.get(field) is None:
        if required:
            _append_unique(errors, f"'{field}' is required and must be a finite number.")
        return
    number = _as_number(mapping.get(field))
    if number is None:
        _append_unique(errors, f"'{field}' must be a finite number.")
        return
    lower_bound = -1.0 if allow_negative else 0.0
    if not lower_bound <= number <= 1.0:
        _append_unique(errors, f"'{field}' must be between {lower_bound:g} and 1.")


def _validate_schema(
    raw: Any,
    schema_errors: list[str],
) -> tuple[Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], Mapping[str, Any], list[Any]]:
    if not _is_mapping(raw):
        _append_unique(schema_errors, "Root configuration must be a mapping/object.")
        return {}, {}, {}, {}, []

    case = _validate_required_mapping(raw, "case", schema_errors)
    financials = _validate_required_mapping(raw, "financials", schema_errors)
    factory = _validate_required_mapping(raw, "factory", schema_errors)
    valuation = _validate_required_mapping(raw, "valuation", schema_errors)

    scenarios_raw = raw.get("scenarios")
    if not _is_sequence(scenarios_raw) or not scenarios_raw:
        _append_unique(schema_errors, "'scenarios' must be a non-empty list.")
        scenarios = []
    else:
        scenarios = list(scenarios_raw)

    for field in ("company_name", "ticker", "point_in_time", "currency"):
        if not isinstance(case.get(field), str) or not case[field].strip():
            _append_unique(schema_errors, f"'case.{field}' is required and must be a non-empty string.")

    point_in_time = case.get("point_in_time")
    if isinstance(point_in_time, str) and point_in_time.strip() and _parse_iso_date(point_in_time) is None:
        _append_unique(schema_errors, "'case.point_in_time' must use ISO date format YYYY-MM-DD.")

    sources = raw.get("sources", [])
    if not _is_sequence(sources):
        _append_unique(schema_errors, "'sources' must be a list when supplied.")

    return case, financials, factory, valuation, scenarios


def _validate_scenarios(scenarios: Sequence[Any], financial_errors: list[str]) -> None:
    names: set[str] = set()
    probabilities: list[float] = []
    required = ("name", "probability", "revenue_cagr", "ebit_margin", "reinvestment_rate", "exit_multiple")

    for index, item in enumerate(scenarios):
        prefix = f"scenarios[{index}]"
        if not _is_mapping(item):
            _append_unique(financial_errors, f"'{prefix}' must be a mapping/object.")
            continue

        for field in required:
            if field not in item or item.get(field) is None:
                _append_unique(financial_errors, f"'{prefix}.{field}' is required.")

        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            _append_unique(financial_errors, f"'{prefix}.name' must be a non-empty string.")
        else:
            normalised = name.strip().lower()
            if normalised in names:
                _append_unique(financial_errors, f"Scenario name '{name.strip()}' is duplicated.")
            names.add(normalised)

        probability = _as_number(item.get("probability"))
        if probability is None:
            _append_unique(financial_errors, f"'{prefix}.probability' must be a finite number.")
        elif not 0.0 <= probability <= 1.0:
            _append_unique(financial_errors, f"'{prefix}.probability' must be between 0 and 1.")
        else:
            probabilities.append(probability)

        _validate_fraction(item, "ebit_margin", financial_errors, required=True, allow_negative=True)
        _validate_fraction(item, "reinvestment_rate", financial_errors, required=True)
        _validate_non_negative(item, "exit_multiple", financial_errors, required=True, strictly_positive=True)

        growth = _as_number(item.get("revenue_cagr"))
        if growth is None:
            _append_unique(financial_errors, f"'{prefix}.revenue_cagr' must be a finite number.")
        elif growth <= -1.0:
            _append_unique(financial_errors, f"'{prefix}.revenue_cagr' must be greater than -1.")

    if probabilities and len(probabilities) == len(scenarios):
        total = sum(probabilities)
        if abs(total - 1.0) > 1e-9:
            _append_unique(
                financial_errors,
                f"Scenario probabilities must sum to 1.0; received {total:.12f}.",
            )


def _validate_financial_consistency(
    financials: Mapping[str, Any],
    factory: Mapping[str, Any],
    valuation: Mapping[str, Any],
    scenarios: Sequence[Any],
    raw: Mapping[str, Any],
    financial_errors: list[str],
) -> None:
    for field in ("market_cap_usd", "cash_usd", "total_debt_usd", "current_revenue_usd", "nol_balance_usd"):
        _validate_non_negative(financials, field, financial_errors)
    _validate_non_negative(financials, "current_shares", financial_errors, strictly_positive=True)
    _validate_non_negative(financials, "share_price_usd", financial_errors, strictly_positive=True)

    for field in (
        "capacity_max_units",
        "current_capacity_units",
        "expansion_capacity_units",
        "asp_usd",
        "variable_cost_per_unit",
        "maintenance_capex_per_unit",
        "incremental_capex_usd",
    ):
        _validate_non_negative(factory, field, financial_errors)
    for field in ("current_utilization", "yield_rate", "top_customer_revenue_pct"):
        _validate_fraction(factory, field, financial_errors)
    for field in ("expansion_lead_time_years", "qualification_lead_time_years", "ramp_years"):
        _validate_non_negative(factory, field, financial_errors)

    forecast_years = _as_number(valuation.get("forecast_years"))
    if valuation.get("forecast_years") is not None:
        if forecast_years is None or forecast_years <= 0 or not forecast_years.is_integer():
            _append_unique(financial_errors, "'valuation.forecast_years' must be a positive integer.")

    for field in ("wacc", "wacc_initial", "wacc_terminal"):
        if field not in valuation or valuation.get(field) is None:
            continue
        value = _as_number(valuation.get(field))
        if value is None:
            _append_unique(financial_errors, f"'valuation.{field}' must be a finite number.")
        elif value <= -1.0:
            _append_unique(financial_errors, f"'valuation.{field}' must be greater than -1.")

    _validate_fraction(valuation, "tax_rate", financial_errors)
    _validate_fraction(valuation, "terminal_growth", financial_errors, allow_negative=True)
    _validate_fraction(valuation, "target_ebit_margin", financial_errors, allow_negative=True)
    _validate_fraction(valuation, "reinvestment_rate", financial_errors)
    _validate_non_negative(valuation, "revenue_to_invested_capital", financial_errors, strictly_positive=True)

    _validate_scenarios(scenarios, financial_errors)
    _validate_debt_dates(raw, financial_errors)


def _validate_debt_dates(raw: Mapping[str, Any], financial_errors: list[str]) -> None:
    debt = raw.get("debt_structure")
    if debt is None:
        return
    if not _is_mapping(debt):
        _append_unique(financial_errors, "'debt_structure' must be a mapping/object when supplied.")
        return

    context = raw.get("debt_context", {})
    current_date = context.get("current_date") if _is_mapping(context) else None
    case = raw.get("case", {})
    case_date = case.get("point_in_time") if _is_mapping(case) else None
    reference_date = _parse_iso_date(current_date) or _parse_iso_date(case_date)

    instruments = debt.get("instruments", [])
    if not _is_sequence(instruments):
        _append_unique(financial_errors, "'debt_structure.instruments' must be a list.")
        return

    for index, instrument in enumerate(instruments):
        prefix = f"debt_structure.instruments[{index}]"
        if not _is_mapping(instrument):
            _append_unique(financial_errors, f"'{prefix}' must be a mapping/object.")
            continue
        _validate_non_negative(instrument, "principal_usd", financial_errors, required=True, strictly_positive=True)
        maturity = _parse_iso_date(instrument.get("maturity_date"))
        if maturity is None:
            _append_unique(financial_errors, f"'{prefix}.maturity_date' must use ISO date format YYYY-MM-DD.")
        elif reference_date is not None and maturity <= reference_date:
            _append_unique(financial_errors, f"'{prefix}.maturity_date' must be after the case point-in-time.")


def _source_index(raw_sources: Any) -> dict[str, list[Mapping[str, Any]]]:
    index: dict[str, list[Mapping[str, Any]]] = {}
    if not _is_sequence(raw_sources):
        return index
    for item in raw_sources:
        if not _is_mapping(item):
            continue
        field = item.get("field")
        if field is None:
            continue
        index.setdefault(_normalise_field_name(field), []).append(item)
    return index


def _material_fields_present(raw: Mapping[str, Any]) -> set[str]:
    present: set[str] = set()
    financials = raw.get("financials", {})
    if _is_mapping(financials):
        present.update(
            _normalise_field_name(key)
            for key, value in financials.items()
            if value is not None and _normalise_field_name(key) in MATERIAL_INPUT_FIELDS
        )

    if raw.get("debt_structure") is not None:
        present.add("debt_structure")
    if raw.get("scenarios"):
        present.add("scenario_assumptions")
    if raw.get("factory"):
        present.add("factory_proxy")
    return present


def _validate_economic_consistency(raw: Mapping[str, Any], warnings: list[str]) -> dict[str, Any]:
    case = raw.get("case", {}) if _is_mapping(raw.get("case")) else {}
    financials = raw.get("financials", {}) if _is_mapping(raw.get("financials")) else {}
    debt_context = raw.get("debt_context", {}) if _is_mapping(raw.get("debt_context")) else {}

    price = _as_number(financials.get("share_price_usd"))
    shares = _as_number(financials.get("current_shares"))
    market_cap = _as_number(financials.get("market_cap_usd"))
    if price is not None and shares is not None and market_cap is not None and market_cap > 0:
        implied = price * shares
        difference = abs(implied - market_cap) / market_cap
        if difference > 0.02:
            _append_unique(warnings, f"market_cap_usd differs by {difference:.1%} from share_price_usd × current_shares.")

    financial_cash = _as_number(financials.get("cash_usd"))
    context_cash = _as_number(debt_context.get("cash_usd"))
    if financial_cash is not None and context_cash is not None and not math.isclose(financial_cash, context_cash, rel_tol=0.0, abs_tol=1.0):
        _append_unique(warnings, "debt_context.cash_usd differs from financials.cash_usd.")

    point_in_time = _parse_iso_date(case.get("point_in_time"))
    debt_date = _parse_iso_date(debt_context.get("current_date"))
    if point_in_time is not None and debt_date is not None and point_in_time != debt_date:
        _append_unique(warnings, "debt_context.current_date differs from case.point_in_time.")

    sources = _source_index(raw.get("sources", []))
    material_fields = _material_fields_present(raw)
    counts = {
        "material_fields_required": len(material_fields),
        "material_fields_with_source": 0,
        "material_fields_with_as_of": 0,
        "material_fields_with_confidence": 0,
    }

    for field in sorted(material_fields):
        entries = sources.get(field, [])
        if not entries:
            _append_unique(warnings, f"Material input '{field}' has no source metadata.")
            continue

        has_source = any(isinstance(entry.get("source"), str) and entry["source"].strip() for entry in entries)
        has_as_of = any(_parse_iso_date(entry.get("as_of")) is not None for entry in entries)
        has_confidence = any(_normalise_field_name(entry.get("confidence")) in VALID_CONFIDENCE for entry in entries)
        if has_source:
            counts["material_fields_with_source"] += 1
        else:
            _append_unique(warnings, f"Material input '{field}' has no source description.")
        if has_as_of:
            counts["material_fields_with_as_of"] += 1
        else:
            _append_unique(warnings, f"Material input '{field}' has no valid as_of date.")
        if has_confidence:
            counts["material_fields_with_confidence"] += 1
        else:
            _append_unique(warnings, f"Material input '{field}' has no valid confidence level.")

    for field, entries in sources.items():
        for entry in entries:
            confidence = entry.get("confidence")
            if confidence is not None and _normalise_field_name(confidence) not in VALID_CONFIDENCE:
                _append_unique(warnings, f"Source '{field}' uses invalid confidence '{confidence}'; use high, medium or low.")
            source_date = _parse_iso_date(entry.get("as_of"))
            if point_in_time is not None and source_date is not None and source_date > point_in_time:
                _append_unique(warnings, f"Source '{field}' has as_of date after case.point_in_time.")
            if _normalise_field_name(confidence) == "low":
                _append_unique(warnings, f"Material evidence '{field}' has low confidence and requires verification.")

    required = counts["material_fields_required"]
    covered = min(
        counts["material_fields_with_source"],
        counts["material_fields_with_as_of"],
        counts["material_fields_with_confidence"],
    )
    counts["coverage_pct"] = 100.0 if required == 0 else round(100.0 * covered / required, 1)
    return counts


def validate_case_raw(raw: Any) -> dict[str, Any]:
    """Valida raw YAML/JSON; retorna estrutura serializável com PASS ou HARD_FAIL."""
    schema_errors: list[str] = []
    financial_errors: list[str] = []
    economic_warnings: list[str] = []

    _, financials, factory, valuation, scenarios = _validate_schema(raw, schema_errors)
    if _is_mapping(raw):
        _validate_financial_consistency(
            financials,
            factory,
            valuation,
            scenarios,
            raw,
            financial_errors,
        )
        input_coverage = _validate_economic_consistency(raw, economic_warnings)
    else:
        input_coverage = {
            "material_fields_required": 0,
            "material_fields_with_source": 0,
            "material_fields_with_as_of": 0,
            "material_fields_with_confidence": 0,
            "coverage_pct": 0.0,
        }

    return {
        "validation_version": VALIDATION_VERSION,
        "status": HARD_FAIL if schema_errors or financial_errors else PASS,
        "schema_errors": schema_errors,
        "financial_errors": financial_errors,
        "economic_warnings": economic_warnings,
        "input_coverage": input_coverage,
    }


def validation_error_message(validation: Mapping[str, Any]) -> str:
    """Converte resultado HARD_FAIL numa mensagem adequada à CLI."""
    errors = [
        *list(validation.get("schema_errors", [])),
        *list(validation.get("financial_errors", [])),
    ]
    if not errors:
        return "Configuration validation failed."
    return "Configuration validation failed:\n- " + "\n- ".join(str(error) for error in errors)
