"""
Case configuration loader — Asymmetry Engine v4.2.0

Lê YAML/JSON, valida a configuração antes de criar dataclasses e corre o
case runner sem editar Python por empresa.

A Validation v1 bloqueia erros de schema e de consistência financeira.
Warnings económicos não bloqueiam a execução, mas são anexados ao resultado
para aparecerem no JSON e no relatório Markdown.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

try:
    import yaml
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "PyYAML é necessário para ficheiros YAML. Instalar com: pip install pyyaml"
    ) from exc

from .case_runner import run_full_case
from .debt_structure_gate import ConvertibleInstrument, DebtStructure
from .expectations_gap_engine import (
    BacklogItem,
    EngineConfig,
    FactoryData,
    FinancialInputs,
    RevenueQualityGate,
    Scenario,
    ValuationAssumptions,
)
from .validation import validate_case_raw, validation_error_message


CONFIG_VERSION = "CASE-CONFIG-4.2.0"


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"'{name}' deve ser um objeto/mapping.")
    return value


def _require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        raise ValueError(f"'{name}' deve ser uma lista.")
    return value


def _plain_dict(value: Mapping[str, Any]) -> Dict[str, Any]:
    return {str(key): item for key, item in value.items()}


def _load_raw_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Config não encontrada: {path}")

    suffix = path.suffix.lower()
    with path.open("r", encoding="utf-8") as handle:
        if suffix in {".yaml", ".yml"}:
            raw = yaml.safe_load(handle)
        elif suffix == ".json":
            raw = json.load(handle)
        else:
            raise ValueError("Config deve terminar em .yaml, .yml ou .json.")

    if raw is None:
        raise ValueError("Config vazia.")
    return _plain_dict(_require_mapping(raw, "root"))


def _build_scenarios(raw: Any) -> list[Scenario]:
    scenarios = []
    for index, item in enumerate(_require_list(raw, "scenarios")):
        data = _plain_dict(_require_mapping(item, f"scenarios[{index}]"))
        scenarios.append(Scenario(**data))

    if not scenarios:
        raise ValueError("A config deve conter pelo menos um cenário.")

    total_probability = sum(scenario.probability for scenario in scenarios)
    if abs(total_probability - 1.0) > 1e-9:
        raise ValueError(
            f"As probabilidades dos cenários devem somar 1.0; recebido {total_probability:.12f}."
        )
    return scenarios


def _build_revenue_quality_gates(raw: Any) -> Dict[str, RevenueQualityGate]:
    if raw is None:
        return {}

    gates_raw = _require_mapping(raw, "revenue_quality_gates")
    gates: Dict[str, RevenueQualityGate] = {}
    for scenario_name, items_raw in gates_raw.items():
        items = []
        for index, item in enumerate(
            _require_list(items_raw, f"revenue_quality_gates.{scenario_name}")
        ):
            data = _plain_dict(
                _require_mapping(item, f"revenue_quality_gates.{scenario_name}[{index}]")
            )
            items.append(BacklogItem(**data))
        gate = RevenueQualityGate(items=items)
        gate.validate()
        gates[str(scenario_name)] = gate
    return gates


def _build_debt_structure(raw: Any) -> Optional[DebtStructure]:
    if raw is None:
        return None

    data = _plain_dict(_require_mapping(raw, "debt_structure"))
    instruments_raw = data.pop("instruments", [])
    instruments = [
        ConvertibleInstrument(
            **_plain_dict(_require_mapping(item, "debt_structure.instruments[]"))
        )
        for item in _require_list(instruments_raw, "debt_structure.instruments")
    ]
    debt = DebtStructure(instruments=instruments, **data)
    debt.validate()
    return debt


def load_case_config(path: str | Path) -> Dict[str, Any]:
    """
    Lê e valida YAML/JSON, depois cria os objetos exigidos pelo case runner.

    Validation v1 corre sobre o dicionário bruto antes da construção de
    dataclasses. Erros bloqueantes levantam ValueError; warnings económicos
    seguem para o resultado final em ``result['validation']``.
    """
    raw = _load_raw_file(Path(path))

    validation = validate_case_raw(raw)
    if validation["status"] == "HARD_FAIL":
        raise ValueError(validation_error_message(validation))

    financials = FinancialInputs(
        **_plain_dict(_require_mapping(raw["financials"], "financials"))
    )
    factory = FactoryData(**_plain_dict(_require_mapping(raw["factory"], "factory")))
    valuation = ValuationAssumptions(
        **_plain_dict(_require_mapping(raw["valuation"], "valuation"))
    )
    config = (
        EngineConfig(**_plain_dict(_require_mapping(raw["engine_config"], "engine_config")))
        if raw.get("engine_config")
        else None
    )
    scenarios = _build_scenarios(raw["scenarios"])
    quality_gates = _build_revenue_quality_gates(raw.get("revenue_quality_gates"))
    debt_structure = _build_debt_structure(raw.get("debt_structure"))

    debt_context = raw.get("debt_context")
    if debt_context is not None:
        debt_context = _plain_dict(_require_mapping(debt_context, "debt_context"))
    if debt_structure is not None and debt_context is None:
        raise ValueError("debt_context é obrigatório quando debt_structure existe.")

    scenario_names = {scenario.name for scenario in scenarios}
    unknown_gate_scenarios = set(quality_gates) - scenario_names
    if unknown_gate_scenarios:
        raise ValueError(
            "Revenue quality gates referem cenários inexistentes: "
            + ", ".join(sorted(unknown_gate_scenarios))
        )

    sources = raw.get("sources", [])
    if not isinstance(sources, list):
        raise ValueError("'sources' deve ser uma lista quando fornecido.")

    return {
        "config_version": CONFIG_VERSION,
        "case": _plain_dict(_require_mapping(raw["case"], "case")),
        "sources": list(sources),
        "validation": validation,
        "financials": financials,
        "factory": factory,
        "valuation": valuation,
        "config": config,
        "scenarios": scenarios,
        "revenue_quality_gates": quality_gates,
        "debt_structure": debt_structure,
        "debt_context": debt_context,
    }


def run_case_config(
    loaded_config: Mapping[str, Any],
    *,
    portfolio: Optional[Mapping[str, float]] = None,
    portfolio_risk_engine: Optional[Any] = None,
    portfolio_confidence: float = 0.95,
    portfolio_impact_metric: str = "H_95",
) -> Dict[str, Any]:
    """Corre um caso já carregado e anexa os metadados auditáveis ao output."""
    validation = loaded_config.get("validation", {})
    if validation.get("status") == "HARD_FAIL":
        raise ValueError(validation_error_message(validation))

    result = run_full_case(
        financials=loaded_config["financials"],
        factory=loaded_config["factory"],
        valuation=loaded_config["valuation"],
        scenarios=loaded_config["scenarios"],
        config=loaded_config.get("config"),
        revenue_quality_gates=loaded_config.get("revenue_quality_gates"),
        debt_structure=loaded_config.get("debt_structure"),
        debt_context=loaded_config.get("debt_context"),
        portfolio=portfolio,
        portfolio_risk_engine=portfolio_risk_engine,
        portfolio_confidence=portfolio_confidence,
        portfolio_impact_metric=portfolio_impact_metric,
    )

    result["case"] = dict(loaded_config.get("case", {}))
    result["sources"] = list(loaded_config.get("sources", []))
    result["validation"] = dict(validation)
    result["config_version"] = loaded_config.get("config_version", CONFIG_VERSION)
    return result
