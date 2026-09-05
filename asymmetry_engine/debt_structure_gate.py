"""
asymmetry_engine/debt_structure_gate.py

Debt structure / maturity wall gate para o Asymmetry Engine.

Esta camada descreve factos de estrutura de capital, sem decidir se um
estado é hard veto, red flag ou watch item. Essa política pertence ao
case_runner.py.

Classificação por instrumento:

- MATURITY_WALL_COVERED: projected cash cobre integralmente o principal.
- MATURITY_WALL_PARTIAL_COVERAGE: existe gap, mas é menor ou igual ao
  threshold configurável de cobertura parcial.
- MATURITY_WALL_UNCOVERED: gap excede o threshold de cobertura parcial.
- BEYOND_FORECAST_HORIZON: maturidade fora do horizonte de forecast.

O default de partial_coverage_max_gap_pct é 50%. Este valor é um threshold
operacional configurável; não é uma afirmação de que um gap parcial seja
inofensivo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math
from typing import Any, Sequence


DEBT_GATE_VERSION = "DEBT-GATE-2.0.0"


@dataclass(frozen=True)
class ConvertibleInstrument:
    principal_usd: float
    conversion_price_usd: float
    maturity_date: str
    coupon_rate: float = 0.0

    def validate(self) -> None:
        if not math.isfinite(float(self.principal_usd)) or self.principal_usd <= 0:
            raise ValueError("principal_usd must be a finite number greater than zero.")
        if not math.isfinite(float(self.conversion_price_usd)) or self.conversion_price_usd <= 0:
            raise ValueError("conversion_price_usd must be a finite number greater than zero.")
        if not math.isfinite(float(self.coupon_rate)):
            raise ValueError("coupon_rate must be finite.")
        try:
            date.fromisoformat(self.maturity_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("maturity_date must use YYYY-MM-DD format.") from exc


@dataclass(frozen=True)
class DebtStructure:
    instruments: Sequence[ConvertibleInstrument] = field(default_factory=list)
    straight_debt_usd: float = 0.0
    partial_coverage_max_gap_pct: float = 0.50

    def validate(self) -> None:
        if not math.isfinite(float(self.straight_debt_usd)) or self.straight_debt_usd < 0:
            raise ValueError("straight_debt_usd must be a finite non-negative number.")
        if not math.isfinite(float(self.partial_coverage_max_gap_pct)):
            raise ValueError("partial_coverage_max_gap_pct must be finite.")
        if not 0.0 <= self.partial_coverage_max_gap_pct <= 1.0:
            raise ValueError("partial_coverage_max_gap_pct must be between 0 and 1.")
        for instrument in self.instruments:
            instrument.validate()


def _parse_date(value: str, label: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must use YYYY-MM-DD format.") from exc


def _years_to_maturity(current_date: date, maturity_date: date) -> float:
    return max(0.0, (maturity_date - current_date).days / 365.25)


def _conversion_status(current_price_usd: float, conversion_price_usd: float) -> tuple[float, str]:
    ratio = current_price_usd / conversion_price_usd
    if ratio >= 1.0:
        return ratio, "IN_THE_MONEY_ACTIVE_DILUTION_PRESSURE"
    if ratio >= 0.85:
        return ratio, "NEAR_THE_MONEY_WATCH"
    return ratio, "OUT_OF_THE_MONEY_NO_PRESSURE"


def _projected_cash(cash_usd: float, annual_fcf_usd: float, years_to_maturity: float) -> float:
    return max(0.0, cash_usd + annual_fcf_usd * years_to_maturity)


def _maturity_wall_status(
    *,
    principal_usd: float,
    refinancing_gap_usd: float,
    gap_pct_of_principal: float,
    partial_coverage_max_gap_pct: float,
) -> str:
    if refinancing_gap_usd <= 1e-6:
        return "MATURITY_WALL_COVERED"
    if gap_pct_of_principal <= partial_coverage_max_gap_pct:
        return "MATURITY_WALL_PARTIAL_COVERAGE"
    return "MATURITY_WALL_UNCOVERED"


def maturity_wall_check(
    debt_structure: DebtStructure,
    *,
    current_price_usd: float,
    current_date: str,
    cash_usd: float,
    annual_fcf_usd: float = 0.0,
    forecast_years: int = 5,
) -> dict[str, Any]:
    """
    Avalia convertíveis e maturity walls dentro do horizonte de forecast.

    O projected cash é calculado separadamente por instrumento a partir de
    cash inicial e FCF acumulado até à respetiva maturidade. Isto é uma proxy
    conservadora de liquidez, não um modelo de cash-flow trimestral.

    A função devolve estados factuais. A política de decisão pertence ao
    case_runner, permitindo tratar partial coverage como red flag ou veto
    conforme o perfil de risco do caso.
    """
    debt_structure.validate()

    for name, value in {
        "current_price_usd": current_price_usd,
        "cash_usd": cash_usd,
        "annual_fcf_usd": annual_fcf_usd,
    }.items():
        if isinstance(value, bool) or not math.isfinite(float(value)):
            raise ValueError(f"{name} must be a finite number.")
    if current_price_usd <= 0:
        raise ValueError("current_price_usd must be greater than zero.")
    if cash_usd < 0:
        raise ValueError("cash_usd must not be negative.")
    if not isinstance(forecast_years, int) or forecast_years <= 0:
        raise ValueError("forecast_years must be a positive integer.")

    today = _parse_date(current_date, "current_date")
    instruments: list[dict[str, Any]] = []
    total_refinancing_gap = 0.0
    uncovered_count = 0
    partial_count = 0
    covered_count = 0
    max_gap_pct = 0.0

    for instrument in debt_structure.instruments:
        maturity = _parse_date(instrument.maturity_date, "maturity_date")
        years = _years_to_maturity(today, maturity)
        conversion_ratio, overhang_status = _conversion_status(
            float(current_price_usd),
            float(instrument.conversion_price_usd),
        )

        result: dict[str, Any] = {
            "principal_usd": float(instrument.principal_usd),
            "conversion_price_usd": float(instrument.conversion_price_usd),
            "maturity_date": instrument.maturity_date,
            "coupon_rate": float(instrument.coupon_rate),
            "years_to_maturity": years,
            "price_to_conversion_ratio": conversion_ratio,
            "overhang_status": overhang_status,
        }

        if years > float(forecast_years):
            result.update({
                "projected_cash_available_usd": None,
                "refinancing_gap_usd": 0.0,
                "gap_pct_of_principal": 0.0,
                "partial_coverage_max_gap_pct": float(debt_structure.partial_coverage_max_gap_pct),
                "wall_status": "BEYOND_FORECAST_HORIZON",
            })
        else:
            projected_cash = _projected_cash(float(cash_usd), float(annual_fcf_usd), years)
            gap = max(0.0, float(instrument.principal_usd) - projected_cash)
            gap_pct = gap / float(instrument.principal_usd)
            wall_status = _maturity_wall_status(
                principal_usd=float(instrument.principal_usd),
                refinancing_gap_usd=gap,
                gap_pct_of_principal=gap_pct,
                partial_coverage_max_gap_pct=float(debt_structure.partial_coverage_max_gap_pct),
            )
            result.update({
                "projected_cash_available_usd": projected_cash,
                "refinancing_gap_usd": gap,
                "gap_pct_of_principal": gap_pct,
                "partial_coverage_max_gap_pct": float(debt_structure.partial_coverage_max_gap_pct),
                "wall_status": wall_status,
            })

            total_refinancing_gap += gap
            max_gap_pct = max(max_gap_pct, gap_pct)
            if wall_status == "MATURITY_WALL_UNCOVERED":
                uncovered_count += 1
            elif wall_status == "MATURITY_WALL_PARTIAL_COVERAGE":
                partial_count += 1
            elif wall_status == "MATURITY_WALL_COVERED":
                covered_count += 1

        instruments.append(result)

    if uncovered_count:
        verdict = "CAPITAL_STRUCTURE_CRISIS_RISK"
    elif partial_count:
        verdict = "PARTIAL_REFINANCING_COVERAGE_RISK"
    else:
        verdict = "MATURITY_WALL_COVERED_OR_BEYOND_HORIZON"

    return {
        "debt_gate_version": DEBT_GATE_VERSION,
        "verdict": verdict,
        "straight_debt_usd": float(debt_structure.straight_debt_usd),
        "partial_coverage_max_gap_pct": float(debt_structure.partial_coverage_max_gap_pct),
        "total_refinancing_gap_usd": total_refinancing_gap,
        "max_gap_pct_of_principal": max_gap_pct,
        "uncovered_instrument_count": uncovered_count,
        "partial_coverage_instrument_count": partial_count,
        "covered_instrument_count": covered_count,
        "instruments": instruments,
    }
