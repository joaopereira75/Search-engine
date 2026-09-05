"""
API publica do package asymmetry_engine.

Guardar como:
    asymmetry_engine/__init__.py

Depois de instalada a pasta como package, permite:

    from asymmetry_engine import run_full_case
    from asymmetry_engine import FinancialInputs, FactoryData

Versao do package: 4.1.0
"""

from .expectations_gap_engine import (
    BacklogItem,
    EngineConfig,
    ExpectationsGapEngine,
    FactoryData,
    FinancialInputs,
    RevenueQualityGate,
    Scenario,
    ValuationAssumptions,
    run_expectations_gap_analysis,
)
from .debt_structure_gate import (
    ConvertibleInstrument,
    DebtStructure,
    maturity_wall_check,
)
from .case_runner import run_full_case

__version__ = "4.1.0"

__all__ = [
    "BacklogItem",
    "ConvertibleInstrument",
    "DebtStructure",
    "EngineConfig",
    "ExpectationsGapEngine",
    "FactoryData",
    "FinancialInputs",
    "RevenueQualityGate",
    "Scenario",
    "ValuationAssumptions",
    "maturity_wall_check",
    "run_expectations_gap_analysis",
    "run_full_case",
]