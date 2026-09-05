"""
asymmetry_engine/sensitivity_engine.py

Sensitivity Engine v1 — análises one-way, reproduzíveis e não destrutivas.

O módulo recebe uma configuração já carregada por ``load_case_config()``,
cria uma cópia profunda por cada valor, altera um driver e executa novamente
o case runner. O YAML, a configuração original e os objetos originais nunca
são modificados.

Exemplo:

    from asymmetry_engine.config import load_case_config
    from asymmetry_engine.sensitivity_engine import run_one_way_sensitivity

    loaded = load_case_config("cases/wolf_2023_08_23.yaml")
    output = run_one_way_sensitivity(
        loaded,
        driver="financials.cash_usd",
        values=[1_000_000_000, 2_000_000_000, 2_954_900_000, 4_500_000_000],
    )

Drivers suportados nesta primeira versão:

- financials.<field>
- factory.<field>
- valuation.<field>
- debt_context.<field>
- scenarios.<ScenarioName>.<field>
- scenarios[<index>].<field>

Para cenários, prefira o nome, por exemplo:

    scenarios.Base.ebit_margin
    scenarios.Bull.revenue_cagr

O output preserva por ponto métricas de valuation, decisão, hard vetoes,
refinancing gap, plausibilidade física e incremental ROIC quando disponível.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import fields, is_dataclass, replace
import math
from typing import Any, Callable, Mapping, MutableMapping, Optional, Sequence


SENSITIVITY_VERSION = "SENSITIVITY-1.0.0"


class SensitivityError(ValueError):
    """Erro de configuração de driver ou de execução da sensitivity."""


def _finite_number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        if hasattr(value, "item"):
            value = value.item()
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _as_plain_value(value: Any) -> Any:
    """Converte escalares numpy para tipos nativos, preservando outros valores."""
    if hasattr(value, "item"):
        return value.item()
    return value


def _read_object_field(container: Any, field_name: str, driver: str) -> Any:
    if isinstance(container, Mapping):
        if field_name not in container:
            raise SensitivityError(f"Driver '{driver}' refers to unknown field '{field_name}'.")
        return container[field_name]
    if is_dataclass(container):
        if not hasattr(container, field_name):
            raise SensitivityError(f"Driver '{driver}' refers to unknown field '{field_name}'.")
        return getattr(container, field_name)
    if not hasattr(container, field_name):
        raise SensitivityError(f"Driver '{driver}' refers to unknown field '{field_name}'.")
    return getattr(container, field_name)


def _write_object_field(container: Any, field_name: str, value: Any, driver: str) -> Any:
    """Atualiza mapping ou devolve dataclass copiada com o field alterado."""
    if isinstance(container, MutableMapping):
        if field_name not in container:
            raise SensitivityError(f"Driver '{driver}' refers to unknown field '{field_name}'.")
        container[field_name] = value
        return container
    if is_dataclass(container):
        valid_fields = {item.name for item in fields(container)}
        if field_name not in valid_fields:
            raise SensitivityError(f"Driver '{driver}' refers to unknown field '{field_name}'.")
        return replace(container, **{field_name: value})
    if not hasattr(container, field_name):
        raise SensitivityError(f"Driver '{driver}' refers to unknown field '{field_name}'.")
    setattr(container, field_name, value)
    return container


def _scenario_index(scenarios: Sequence[Any], reference: str, driver: str) -> int:
    if reference.startswith("[") and reference.endswith("]"):
        index_text = reference[1:-1]
        try:
            index = int(index_text)
        except ValueError as exc:
            raise SensitivityError(f"Driver '{driver}' has invalid scenario index '{reference}'.") from exc
        if index < 0 or index >= len(scenarios):
            raise SensitivityError(f"Driver '{driver}' scenario index {index} is outside the scenario list.")
        return index

    for index, scenario in enumerate(scenarios):
        name = getattr(scenario, "name", None)
        if str(name).lower() == reference.lower():
            return index
    available = ", ".join(str(getattr(item, "name", index)) for index, item in enumerate(scenarios))
    raise SensitivityError(
        f"Driver '{driver}' refers to unknown scenario '{reference}'. Available scenarios: {available}."
    )


def _parse_driver(driver: str) -> tuple[str, str, Optional[str]]:
    """
    Devolve (root, field, scenario_reference).

    Exemplo: scenarios.Base.ebit_margin -> ('scenarios', 'ebit_margin', 'Base')
    """
    if not isinstance(driver, str) or not driver.strip():
        raise SensitivityError("driver must be a non-empty string.")
    driver = driver.strip()

    if driver.startswith("scenarios["):
        closing = driver.find("]")
        if closing == -1 or closing + 2 > len(driver) or driver[closing + 1] != ".":
            raise SensitivityError(
                "Scenario index driver must use format scenarios[<index>].<field>."
            )
        scenario_ref = driver[len("scenarios"):closing + 1]
        field_name = driver[closing + 2:]
        if not field_name or "." in field_name:
            raise SensitivityError(
                "Scenario index driver must use format scenarios[<index>].<field>."
            )
        return "scenarios", field_name, scenario_ref

    parts = driver.split(".")
    if parts[0] == "scenarios":
        if len(parts) != 3 or not parts[1] or not parts[2]:
            raise SensitivityError(
                "Scenario driver must use format scenarios.<ScenarioName>.<field>."
            )
        return "scenarios", parts[2], parts[1]

    if len(parts) != 2 or parts[0] not in {"financials", "factory", "valuation", "debt_context"}:
        raise SensitivityError(
            "Unsupported driver. Use financials.<field>, factory.<field>, valuation.<field>, "
            "debt_context.<field>, scenarios.<ScenarioName>.<field>, or scenarios[<index>].<field>."
        )
    return parts[0], parts[1], None


def get_driver_value(loaded_config: Mapping[str, Any], driver: str) -> Any:
    """Lê o valor atual de um driver suportado sem alterar a configuração."""
    root, field_name, scenario_ref = _parse_driver(driver)
    if root == "scenarios":
        scenarios = loaded_config.get("scenarios")
        if not isinstance(scenarios, Sequence):
            raise SensitivityError("Loaded configuration has no scenario sequence.")
        index = _scenario_index(scenarios, scenario_ref or "", driver)
        return _read_object_field(scenarios[index], field_name, driver)

    container = loaded_config.get(root)
    if container is None:
        raise SensitivityError(f"Driver '{driver}' requires '{root}', which is absent from this case.")
    return _read_object_field(container, field_name, driver)


def set_driver_value(loaded_config: MutableMapping[str, Any], driver: str, value: Any) -> None:
    """Altera um driver numa cópia da configuração carregada."""
    root, field_name, scenario_ref = _parse_driver(driver)
    if root == "scenarios":
        scenarios = loaded_config.get("scenarios")
        if not isinstance(scenarios, list):
            raise SensitivityError("Loaded configuration has no mutable scenario list.")
        index = _scenario_index(scenarios, scenario_ref or "", driver)
        scenarios[index] = _write_object_field(scenarios[index], field_name, value, driver)
        return

    container = loaded_config.get(root)
    if container is None:
        raise SensitivityError(f"Driver '{driver}' requires '{root}', which is absent from this case.")
    replacement = _write_object_field(container, field_name, value, driver)
    if replacement is not container:
        loaded_config[root] = replacement


def _dominant_scenario(result: Mapping[str, Any]) -> Optional[str]:
    scenarios = result.get("valuation", {}).get("scenario_valuation", [])
    if not scenarios:
        return None

    def contribution(scenario: Mapping[str, Any]) -> float:
        probability = _finite_number(scenario.get("probability")) or 0.0
        equity_value = _finite_number(scenario.get("equity_value_usd")) or 0.0
        return probability * equity_value

    winner = max(scenarios, key=contribution)
    return str(winner.get("scenario")) if winner.get("scenario") is not None else None


def _base_scenario_result(result: Mapping[str, Any]) -> Mapping[str, Any]:
    base_name = result.get("base_scenario")
    scenarios = result.get("valuation", {}).get("scenario_valuation", [])
    for scenario in scenarios:
        if scenario.get("scenario") == base_name:
            return scenario
    return {}


def _max_physical_gap(result: Mapping[str, Any]) -> Optional[float]:
    values: list[float] = []
    for scenario in result.get("valuation", {}).get("scenario_valuation", []):
        gap = _finite_number(scenario.get("physical_feasibility", {}).get("physical_feasibility_gap"))
        if gap is not None:
            values.append(gap)
    return max(values) if values else None


def _incremental_roic(result: Mapping[str, Any]) -> Optional[float]:
    base = _base_scenario_result(result)
    candidate_paths = (
        base.get("incremental_roic"),
        base.get("incremental_roic_pct"),
        result.get("valuation", {}).get("incremental_roic"),
        result.get("valuation", {}).get("incremental_roic_pct"),
    )
    for value in candidate_paths:
        number = _finite_number(value)
        if number is not None:
            return number
    return None


def _refinancing_gap(result: Mapping[str, Any]) -> Optional[float]:
    debt = result.get("debt_structure")
    if not isinstance(debt, Mapping):
        return None
    return _finite_number(debt.get("total_refinancing_gap_usd"))


def extract_snapshot(result: Mapping[str, Any]) -> dict[str, Any]:
    """Extrai um snapshot compacto e estável de uma execução completa."""
    valuation = result.get("valuation", {})
    asymmetry = valuation.get("asymmetry", {})
    weighted = valuation.get("probability_weighted_valuation", {})

    return {
        "expected_equity_value_usd": _finite_number(weighted.get("expected_equity_value_usd")),
        "expected_return": _finite_number(weighted.get("expected_return")),
        "asymmetry_ratio": _finite_number(asymmetry.get("asymmetry_ratio")),
        "asymmetry_verdict": asymmetry.get("verdict"),
        "decision": result.get("decision"),
        "hard_vetoes": list(result.get("hard_vetoes", [])),
        "red_flags": list(result.get("red_flags", [])),
        "watch_items": list(result.get("watch_items", [])),
        "refinancing_gap_usd": _refinancing_gap(result),
        "physical_feasibility_gap": _max_physical_gap(result),
        "base_scenario_physical_feasibility_gap": _finite_number(
            _base_scenario_result(result).get("physical_feasibility", {}).get("physical_feasibility_gap")
        ),
        "incremental_roic": _incremental_roic(result),
        "dominant_scenario": _dominant_scenario(result),
    }


def _decision_rank(value: Any) -> int:
    """Escala ordinal para comparar flips; não substitui a decisão do runner."""
    ranks = {
        "WATCHLIST / REJECT": 0,
        "PILOT CANDIDATE": 1,
        "CORE CANDIDATE": 2,
    }
    return ranks.get(str(value), -1)


def _point_has_decision_flip(baseline: Mapping[str, Any], point: Mapping[str, Any]) -> bool:
    return point.get("decision") != baseline.get("decision") or point.get("hard_vetoes") != baseline.get("hard_vetoes")


def _impact_score(baseline: Mapping[str, Any], point: Mapping[str, Any]) -> float:
    """Score simples para ordenar pontos, com peso alto para flips de decisão/veto."""
    score = 0.0
    if point.get("decision") != baseline.get("decision"):
        score += 100.0 + 25.0 * abs(_decision_rank(point.get("decision")) - _decision_rank(baseline.get("decision")))
    if point.get("hard_vetoes") != baseline.get("hard_vetoes"):
        score += 75.0

    baseline_return = _finite_number(baseline.get("expected_return"))
    point_return = _finite_number(point.get("expected_return"))
    if baseline_return is not None and point_return is not None:
        score += min(50.0, abs(point_return - baseline_return) * 100.0)

    baseline_asymmetry = _finite_number(baseline.get("asymmetry_ratio"))
    point_asymmetry = _finite_number(point.get("asymmetry_ratio"))
    if baseline_asymmetry is not None and point_asymmetry is not None:
        score += min(25.0, abs(point_asymmetry - baseline_asymmetry) * 10.0)

    baseline_gap = _finite_number(baseline.get("refinancing_gap_usd"))
    point_gap = _finite_number(point.get("refinancing_gap_usd"))
    if baseline_gap is not None and point_gap is not None and baseline_gap > 0:
        score += min(50.0, abs(point_gap - baseline_gap) / baseline_gap * 50.0)
    return round(score, 6)


def run_one_way_sensitivity(
    loaded_config: Mapping[str, Any],
    *,
    driver: str,
    values: Sequence[Any],
    runner: Optional[Callable[[Mapping[str, Any]], Mapping[str, Any]]] = None,
) -> dict[str, Any]:
    """
    Corre sensitivity one-way para um driver e uma sequência de valores.

    Parameters
    ----------
    loaded_config:
        Output de ``load_case_config()`` já validado.
    driver:
        Caminho do input a variar. Ver formatos suportados no módulo.
    values:
        Valores a testar. Devem conter pelo menos um item.
    runner:
        Função opcional para facilitar testes. Por defeito importa e usa
        ``run_case_config`` do package.
    """
    if not isinstance(loaded_config, Mapping):
        raise SensitivityError("loaded_config must be a mapping returned by load_case_config().")
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)) or not values:
        raise SensitivityError("values must be a non-empty sequence.")

    # Valida o driver logo no início e guarda o valor original para auditoria.
    baseline_value = _as_plain_value(get_driver_value(loaded_config, driver))

    if runner is None:
        from .config import run_case_config
        runner = run_case_config

    baseline_result = dict(runner(deepcopy(loaded_config)))
    baseline = extract_snapshot(baseline_result)

    points: list[dict[str, Any]] = []
    for raw_value in values:
        value = _as_plain_value(raw_value)
        candidate = deepcopy(loaded_config)
        set_driver_value(candidate, driver, value)
        execution = dict(runner(candidate))
        snapshot = extract_snapshot(execution)
        snapshot.update({
            "driver": driver,
            "value": value,
            "decision_flip": _point_has_decision_flip(baseline, snapshot),
            "impact_score": _impact_score(baseline, snapshot),
        })
        points.append(snapshot)

    points_sorted_by_impact = sorted(
        points,
        key=lambda point: (float(point["impact_score"]), str(point["value"])),
        reverse=True,
    )

    return {
        "sensitivity_version": SENSITIVITY_VERSION,
        "analysis_type": "one_way",
        "driver": driver,
        "baseline_driver_value": baseline_value,
        "baseline": baseline,
        "points": points,
        "points_ranked_by_impact": points_sorted_by_impact,
        "decision_flip_points": [point for point in points if point["decision_flip"]],
    }
