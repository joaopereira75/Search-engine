"""
asymmetry_engine/cli.py

Command-line interface do Asymmetry Engine.

Exemplos:

    python -m asymmetry_engine.cli validate cases/wolf_2023_08_23.yaml

    python -m asymmetry_engine.cli run cases/wolf_2023_08_23.yaml \
        --output outputs/wolf_result.json \
        --report outputs/wolf_report.md

    python -m asymmetry_engine.cli sensitivity cases/wolf_2023_08_23.yaml \
        --driver financials.cash_usd \
        --values 1000000000 2000000000 2954900000 3500000000 4500000000 \
        --output outputs/wolf_cash_sensitivity.json

    python -m asymmetry_engine.cli breakeven cases/wolf_2023_08_23.yaml \
        --type cash-veto --lower 0 --upper 10000000000 \
        --output outputs/wolf_cash_breakeven.json

    python -m asymmetry_engine.cli research cases/wolf_2023_08_23.yaml \
        --output outputs/wolf_research.json \
        --report outputs/wolf_research.md
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .breakeven_engine import (
    BreakEvenError,
    solve_maximum_fcf_burn_without_hard_veto,
    solve_minimum_asp_for_scenario_feasibility,
    solve_minimum_cash_to_remove_hard_veto,
    solve_minimum_value_for_decision,
)
from .config import load_case_config, run_case_config
from .reporting import write_markdown_report
from .research_pipeline import ResearchPipelineError, run_research_pipeline
from .sensitivity_engine import SensitivityError, run_one_way_sensitivity


CLI_VERSION = "CLI-4.4.1"


def _json_default(value: Any) -> Any:
    if hasattr(value, "item"):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Objeto não serializável: {type(value).__name__}")


def _sanitize_for_json(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {str(key): _sanitize_for_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_for_json(item) for item in value]
    if hasattr(value, "item"):
        return _sanitize_for_json(value.item())
    return value


def _write_json(path: Path, payload: Dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(
            _sanitize_for_json(payload),
            handle,
            indent=2,
            ensure_ascii=False,
            default=_json_default,
        )
        handle.write("\n")
    return path


def _format_currency(value: Any, currency: str = "USD") -> str:
    if value is None:
        return "n/a"
    try:
        return f"{currency} {float(value):,.0f}"
    except (TypeError, ValueError):
        return "n/a"


def _format_pct(value: Any) -> str:
    if value is None:
        return "n/a"
    try:
        return f"{float(value):.1%}"
    except (TypeError, ValueError):
        return "n/a"


def _format_driver_value(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            return "n/a"
        return f"{number:,.6g}"
    return str(value)


def _short_vetoes(vetoes: Any) -> str:
    if not vetoes:
        return "none"
    if not isinstance(vetoes, list):
        return str(vetoes)
    return ", ".join(str(veto) for veto in vetoes)


def _print_case_summary(result: Dict[str, Any]) -> None:
    case = result.get("case", {})
    valuation = result.get("valuation", {})
    asymmetry = valuation.get("asymmetry", {})
    weighted = valuation.get("probability_weighted_valuation", {})
    currency = case.get("currency", "USD")

    print()
    print("=" * 72)
    print(f"{case.get('company_name', 'Unnamed case')} ({case.get('ticker', 'N/A')})")
    print(f"Point in time: {case.get('point_in_time', 'N/A')}")
    print("=" * 72)
    print(f"Decision:         {result.get('decision', 'N/A')}")
    print(f"Base scenario:    {result.get('base_scenario', 'N/A')}")
    print(f"Asymmetry ratio:  {asymmetry.get('asymmetry_ratio', 'N/A')}")
    print(f"Asymmetry status: {asymmetry.get('verdict', 'N/A')}")
    print("Expected equity:  " + _format_currency(weighted.get("expected_equity_value_usd"), currency))
    print("Expected return:  " + _format_pct(weighted.get("expected_return")))

    validation = result.get("validation", {})
    if validation:
        print(f"Validation:       {validation.get('status', 'N/A')}")
        warnings = validation.get("economic_warnings", [])
        if warnings:
            print(f"Validation warnings: {len(warnings)}")

    for title, items, empty in (
        ("Hard vetoes", result.get("hard_vetoes", []), "None"),
        ("Red flags", result.get("red_flags", []), "None"),
        ("Watch items", result.get("watch_items", []), "None"),
    ):
        print()
        print(f"{title}:")
        if items:
            for item in items:
                print(f"  - {item}")
        else:
            print(f"  - {empty}")

    debt = result.get("debt_structure")
    if debt is not None:
        print()
        print(f"Debt verdict:     {debt.get('verdict', 'N/A')}")
        print("Refinancing gap: " + _format_currency(debt.get("total_refinancing_gap_usd"), currency))

    print()
    print("Scenario results:")
    print(f"{'Scenario':<28} {'Probability':>12} {'Return':>12} {'Equity value':>18}")
    print("-" * 72)
    for scenario in valuation.get("scenario_valuation", []):
        name = str(scenario.get("scenario", "N/A"))[:28]
        probability = float(scenario.get("probability", 0.0))
        scenario_return = float(scenario.get("return_pct", 0.0))
        equity_value = _format_currency(scenario.get("equity_value_usd"), currency)
        print(f"{name:<28} {probability:>11.0%} {scenario_return:>11.1%} {equity_value:>18}")
    print("=" * 72)


def _print_sensitivity_summary(sensitivity: Dict[str, Any], currency: str) -> None:
    baseline = sensitivity.get("baseline", {})
    points = sensitivity.get("points", [])
    driver = sensitivity.get("driver", "n/a")

    print()
    print("=" * 116)
    print(f"One-way sensitivity — {driver}")
    print(f"Baseline driver value: {_format_driver_value(sensitivity.get('baseline_driver_value'))}")
    print(
        "Baseline: "
        f"{baseline.get('decision', 'n/a')} | "
        f"return {_format_pct(baseline.get('expected_return'))} | "
        f"refinancing gap {_format_currency(baseline.get('refinancing_gap_usd'), currency)} | "
        f"vetoes {_short_vetoes(baseline.get('hard_vetoes'))}"
    )
    print("=" * 116)
    print(
        f"{'Value':>16}  {'Decision':<20}  {'Expected return':>15}  "
        f"{'Asymmetry':>11}  {'Refinancing gap':>18}  {'Flip':<5}  Hard vetoes"
    )
    print("-" * 116)

    for point in points:
        decision = str(point.get("decision", "n/a"))[:20]
        return_text = _format_pct(point.get("expected_return"))
        asymmetry = point.get("asymmetry_ratio")
        asymmetry_text = "n/a" if asymmetry is None else f"{float(asymmetry):.2f}x"
        gap_text = _format_currency(point.get("refinancing_gap_usd"), currency)
        flip = "YES" if point.get("decision_flip") else "no"
        vetoes = _short_vetoes(point.get("hard_vetoes"))
        print(
            f"{_format_driver_value(point.get('value')):>16}  {decision:<20}  "
            f"{return_text:>15}  {asymmetry_text:>11}  {gap_text:>18}  {flip:<5}  {vetoes}"
        )
    print("=" * 116)

    flips = sensitivity.get("decision_flip_points", [])
    if flips:
        print("Decision / veto flip points:")
        for point in flips:
            print(
                f"  - {_format_driver_value(point.get('value'))}: "
                f"{point.get('decision')} | vetoes: {_short_vetoes(point.get('hard_vetoes'))}"
            )
    else:
        print("No decision or hard-veto flip was found within the tested values.")


def _print_breakeven_summary(solution: Dict[str, Any], currency: str) -> None:
    status = solution.get("status", "n/a")
    label = solution.get("label", "Break-even")
    driver = solution.get("driver", "n/a")
    baseline = solution.get("baseline", {})
    threshold = solution.get("threshold")

    print()
    print("=" * 88)
    print(f"Break-even solver — {label}")
    print("=" * 88)
    print(f"Status:                {status}")
    print(f"Driver:                {driver}")
    print(f"Original driver value: {_format_driver_value(solution.get('original_driver_value'))}")
    print(f"Search resolution:     {_format_driver_value(solution.get('resolution'))}")
    print(f"Iterations:            {solution.get('iterations', 0)}")
    print()
    print("Baseline:")
    print(f"  Decision:             {baseline.get('decision', 'n/a')}")
    print(f"  Expected return:      {_format_pct(baseline.get('expected_return'))}")
    print(f"  Asymmetry ratio:      {_format_driver_value(baseline.get('asymmetry_ratio'))}x")
    print(f"  Refinancing gap:      {_format_currency(baseline.get('refinancing_gap_usd'), currency)}")
    print(f"  Hard vetoes:          {_short_vetoes(baseline.get('hard_vetoes'))}")

    if threshold is None:
        print()
        print("No threshold satisfying the requested condition was found within the supplied range.")
        print("Increase or widen the range only if the assumption remains economically plausible.")
        print("=" * 88)
        return

    print()
    print("Threshold result:")
    print(f"  Threshold value:      {_format_driver_value(solution.get('threshold_value'))}")
    print(f"  Decision:             {threshold.get('decision', 'n/a')}")
    print(f"  Expected return:      {_format_pct(threshold.get('expected_return'))}")
    print(f"  Asymmetry ratio:      {_format_driver_value(threshold.get('asymmetry_ratio'))}x")
    print(f"  Refinancing gap:      {_format_currency(threshold.get('refinancing_gap_usd'), currency)}")
    print(f"  Hard vetoes:          {_short_vetoes(threshold.get('hard_vetoes'))}")
    print(f"  Dominant scenario:    {threshold.get('dominant_scenario', 'n/a')}")
    print("=" * 88)


def _print_research_summary(package: Dict[str, Any]) -> None:
    base_case = package.get("base_case", {})
    case = package.get("case", {})
    ranking = package.get("driver_ranking", [])
    breakevens = package.get("breakevens", [])
    errors = package.get("research_errors", [])
    currency = case.get("currency", "USD")

    print()
    print("=" * 96)
    print(f"Research package — {case.get('company_name', 'Unnamed case')} ({case.get('ticker', 'N/A')})")
    print(f"Point in time: {case.get('point_in_time', 'N/A')}")
    print("=" * 96)
    print(f"Base decision:   {base_case.get('decision', 'n/a')}")
    print(f"Hard vetoes:     {_short_vetoes(base_case.get('hard_vetoes'))}")
    print(f"Red flags:       {_short_vetoes(base_case.get('red_flags'))}")

    validation = package.get("validation", {})
    if validation:
        coverage = validation.get("input_coverage", {})
        print(f"Validation:      {validation.get('status', 'n/a')}")
        if coverage:
            print(f"Evidence cover:  {coverage.get('coverage_pct', 'n/a')}%")
        warnings = validation.get("economic_warnings", [])
        if warnings:
            print(f"Warnings:        {len(warnings)}")

    print()
    print("Top decision drivers:")
    if ranking:
        print(f"{'Rank':>4}  {'Materiality':<11}  {'Driver':<38}  {'Impact':>9}  {'Flips':>7}  {'Veto flips':>11}")
        print("-" * 96)
        for item in ranking:
            print(
                f"{int(item.get('rank', 0)):>4}  "
                f"{str(item.get('materiality', 'n/a')):<11}  "
                f"{str(item.get('label', item.get('driver', 'n/a')))[:38]:<38}  "
                f"{float(item.get('impact_score', 0.0)):>9.2f}  "
                f"{int(item.get('decision_flip_count', 0)):>7}  "
                f"{int(item.get('hard_veto_flip_count', 0)):>11}"
            )
    else:
        print("No sensitivity results were produced.")

    print()
    print("Break-even conditions:")
    if breakevens:
        for solution in breakevens:
            threshold = solution.get("threshold")
            value = solution.get("threshold_value")
            if value is None:
                print(f"  - {solution.get('label', 'Break-even')}: no solution within tested range")
                continue
            gap = threshold.get("refinancing_gap_usd") if threshold else None
            print(
                f"  - {solution.get('label', 'Break-even')}: "
                f"{_format_driver_value(value)} "
                f"| refinancing gap {_format_currency(gap, currency)}"
            )
    else:
        print("No break-even results were produced.")

    if errors:
        print()
        print("Research items requiring review:")
        for error in errors:
            print(f"  - {error.get('stage', 'research')}[{error.get('item', '?')}]: {error.get('error', 'unknown error')}")
    print("=" * 96)


def _run_one_case(
    path: Path,
    *,
    output_json: Optional[Path] = None,
    output_report: Optional[Path] = None,
) -> Dict[str, Any]:
    loaded_config = load_case_config(path)
    result = run_case_config(loaded_config)
    _print_case_summary(result)

    if output_json is not None:
        _write_json(output_json, result)
        print(f"Resultado JSON guardado em: {output_json}")
    if output_report is not None:
        write_markdown_report(result, output_report)
        print(f"Relatório Markdown guardado em: {output_report}")
    return result


def _yaml_files(directory: Path) -> Iterable[Path]:
    return sorted(
        path
        for path in directory.iterdir()
        if path.is_file() and path.suffix.lower() in {".yaml", ".yml"}
    )


def command_run(args: argparse.Namespace) -> int:
    _run_one_case(
        Path(args.case),
        output_json=Path(args.output) if args.output else None,
        output_report=Path(args.report) if args.report else None,
    )
    return 0


def command_validate(args: argparse.Namespace) -> int:
    loaded = load_case_config(Path(args.case))
    case = loaded.get("case", {})
    validation = loaded.get("validation", {})
    print(f"Config válida: {case.get('company_name', 'Unnamed case')} ({case.get('ticker', 'N/A')})")
    print(f"Validation status: {validation.get('status', 'PASS')}")
    coverage = validation.get("input_coverage", {})
    if coverage:
        print(f"Evidence coverage: {coverage.get('coverage_pct', 'n/a')}%")
    warnings = validation.get("economic_warnings", [])
    if warnings:
        print("Economic warnings:")
        for warning in warnings:
            print(f"  - {warning}")
    return 0


def command_sensitivity(args: argparse.Namespace) -> int:
    loaded = load_case_config(Path(args.case))
    sensitivity = run_one_way_sensitivity(loaded, driver=args.driver, values=args.values)
    currency = loaded.get("case", {}).get("currency", "USD")
    _print_sensitivity_summary(sensitivity, currency)
    if args.output:
        output_path = _write_json(Path(args.output), sensitivity)
        print(f"Sensitivity JSON guardado em: {output_path}")
    return 0


def command_breakeven(args: argparse.Namespace) -> int:
    loaded = load_case_config(Path(args.case))
    common = {"lower": args.lower, "upper": args.upper, "resolution": args.resolution}

    if args.break_even_type == "cash-veto":
        solution = solve_minimum_cash_to_remove_hard_veto(
            loaded,
            hard_veto=args.hard_veto,
            cash_driver=args.driver or "debt_context.cash_usd",
            **common,
        )
    elif args.break_even_type == "fcf-veto":
        solution = solve_maximum_fcf_burn_without_hard_veto(
            loaded,
            hard_veto=args.hard_veto,
            fcf_driver=args.driver or "debt_context.annual_fcf_usd",
            **common,
        )
    elif args.break_even_type == "asp-feasibility":
        if not args.scenario:
            raise BreakEvenError("--scenario is required when --type asp-feasibility.")
        solution = solve_minimum_asp_for_scenario_feasibility(
            loaded,
            scenario_name=args.scenario,
            asp_driver=args.driver or "factory.asp_usd",
            **common,
        )
    elif args.break_even_type == "decision":
        if not args.driver:
            raise BreakEvenError("--driver is required when --type decision.")
        solution = solve_minimum_value_for_decision(
            loaded,
            driver=args.driver,
            lower=args.lower,
            upper=args.upper,
            resolution=args.resolution,
        )
    else:  # pragma: no cover
        raise BreakEvenError(f"Unknown break-even type: {args.break_even_type}")

    currency = loaded.get("case", {}).get("currency", "USD")
    _print_breakeven_summary(solution, currency)
    if args.output:
        output_path = _write_json(Path(args.output), solution)
        print(f"Break-even JSON guardado em: {output_path}")
    return 0


def command_research(args: argparse.Namespace) -> int:
    loaded = load_case_config(Path(args.case))
    package = run_research_pipeline(loaded, continue_on_error=not args.fail_fast)
    _print_research_summary(package)

    if args.output:
        output_path = _write_json(Path(args.output), package)
        print(f"Research JSON guardado em: {output_path}")
    if args.report:
        # Importante: passa o package inteiro. reporting.py identifica o
        # research package e renderiza validation, ranking, break-evens,
        # sensitivities e research errors, além do caso-base.
        report_path = write_markdown_report(package, Path(args.report))
        print(f"Relatório Markdown de research guardado em: {report_path}")
    return 0


def command_run_all(args: argparse.Namespace) -> int:
    cases_dir = Path(args.cases_directory)
    if not cases_dir.is_dir():
        raise FileNotFoundError(f"Diretório de casos não encontrado: {cases_dir}")
    files = list(_yaml_files(cases_dir))
    if not files:
        raise FileNotFoundError(f"Não foram encontrados ficheiros YAML em: {cases_dir}")

    output_dir = Path(args.output_dir) if args.output_dir else None
    report_dir = Path(args.report_dir) if args.report_dir else None
    failures = []

    for case_path in files:
        try:
            json_path = output_dir / f"{case_path.stem}_result.json" if output_dir else None
            report_path = report_dir / f"{case_path.stem}_report.md" if report_dir else None
            _run_one_case(case_path, output_json=json_path, output_report=report_path)
        except Exception as error:
            failures.append((case_path.name, str(error)))
            print(f"ERRO em {case_path.name}: {error}")

    if failures:
        print()
        print(f"{len(failures)} caso(s) falharam:")
        for filename, error in failures:
            print(f"  - {filename}: {error}")
        return 1

    print()
    print(f"Todos os {len(files)} casos terminaram sem erro.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="asymmetry-engine",
        description="Executa valuation, gates, validation, sensitivities, break-even, research e relatórios a partir de configs YAML.",
    )
    parser.add_argument("--version", action="version", version=CLI_VERSION)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Corre um ficheiro YAML ou JSON de caso.")
    run_parser.add_argument("case", help="Caminho para o ficheiro de caso YAML ou JSON.")
    run_parser.add_argument("--output", help="Caminho opcional para guardar o resultado estruturado em JSON.")
    run_parser.add_argument("--report", help="Caminho opcional para guardar um relatório auditável em Markdown (.md).")
    run_parser.set_defaults(handler=command_run)

    validate_parser = subparsers.add_parser("validate", help="Valida config sem correr valuation.")
    validate_parser.add_argument("case", help="Caminho para o ficheiro de caso YAML ou JSON.")
    validate_parser.set_defaults(handler=command_validate)

    sensitivity_parser = subparsers.add_parser("sensitivity", help="Corre uma sensitivity one-way sem alterar o YAML original.")
    sensitivity_parser.add_argument("case", help="Caminho para o ficheiro de caso YAML ou JSON.")
    sensitivity_parser.add_argument(
        "--driver",
        required=True,
        help="Driver: financials.<field>, factory.<field>, valuation.<field>, debt_context.<field>, scenarios.<Name>.<field> ou scenarios[index].<field>.",
    )
    sensitivity_parser.add_argument("--values", required=True, nargs="+", type=float, help="Valores numéricos a testar, separados por espaços.")
    sensitivity_parser.add_argument("--output", help="Caminho opcional para guardar o resultado completo em JSON.")
    sensitivity_parser.set_defaults(handler=command_sensitivity)

    breakeven_parser = subparsers.add_parser("breakeven", help="Resolve um threshold ou decision flip por pesquisa binária.")
    breakeven_parser.add_argument("case", help="Caminho para o ficheiro de caso YAML ou JSON.")
    breakeven_parser.add_argument(
        "--type",
        dest="break_even_type",
        required=True,
        choices=("cash-veto", "fcf-veto", "asp-feasibility", "decision"),
        help="Tipo de threshold: cash-veto, fcf-veto, asp-feasibility ou decision.",
    )
    breakeven_parser.add_argument("--lower", required=True, type=float, help="Limite inferior da pesquisa.")
    breakeven_parser.add_argument("--upper", required=True, type=float, help="Limite superior da pesquisa.")
    breakeven_parser.add_argument("--resolution", type=float, default=None, help="Precisão absoluta desejada.")
    breakeven_parser.add_argument("--hard-veto", default="capital_structure_crisis_risk", help="Hard veto a remover nos tipos cash-veto e fcf-veto.")
    breakeven_parser.add_argument("--scenario", help="Cenário para asp-feasibility, por exemplo Bull.")
    breakeven_parser.add_argument("--driver", help="Override do driver; obrigatório para --type decision.")
    breakeven_parser.add_argument("--output", help="Caminho opcional para guardar o resultado completo em JSON.")
    breakeven_parser.set_defaults(handler=command_breakeven)

    research_parser = subparsers.add_parser(
        "research",
        help="Corre caso-base, plano default de sensitivities, break-evens e ranking de drivers.",
    )
    research_parser.add_argument("case", help="Caminho para o ficheiro de caso YAML ou JSON.")
    research_parser.add_argument("--output", help="Caminho opcional para guardar o research package completo em JSON.")
    research_parser.add_argument("--report", help="Caminho opcional para relatório Markdown completo de research.")
    research_parser.add_argument(
        "--fail-fast",
        action="store_true",
        help="Interrompe se uma sensitivity ou break-even individual falhar.",
    )
    research_parser.set_defaults(handler=command_research)

    run_all_parser = subparsers.add_parser("run-all", help="Corre todos os YAMLs num diretório.")
    run_all_parser.add_argument("cases_directory", help="Diretório que contém os casos YAML.")
    run_all_parser.add_argument("--output-dir", help="Diretório opcional para guardar um JSON por caso.")
    run_all_parser.add_argument("--report-dir", help="Diretório opcional para guardar um relatório Markdown por caso.")
    run_all_parser.set_defaults(handler=command_run_all)

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args))
    except (FileNotFoundError, ImportError, ValueError, SensitivityError, BreakEvenError, ResearchPipelineError) as error:
        parser.error(str(error))
    except KeyboardInterrupt:
        print("\nExecução interrompida.")
        return 130
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
