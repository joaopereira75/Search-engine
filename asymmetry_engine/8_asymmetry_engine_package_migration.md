# Migração para package Python importável — Asymmetry Engine

## Objetivo

Os ficheiros atuais foram numerados para facilitar a entrega no chat, mas nomes que começam por número não podem ser importados com sintaxe Python normal. Esta migração define a estrutura final importável do projeto, sem alterar a lógica dos motores.

## Estrutura alvo

```text
asymmetry_engine/
├── __init__.py
├── expectations_gap_engine.py
├── portfolio_risk_engine.py
├── debt_structure_gate.py
├── case_runner.py
├── constants.py
├── data_providers.py
├── reporting.py
├── config.py
└── tests/
    ├── __init__.py
    ├── test_regression.py
    ├── test_expectations_gap_engine.py
    ├── test_debt_structure_gate.py
    └── test_case_runner.py

cases/
├── poet_2024_05_15.yaml
├── aehr_2023_11_30.yaml
├── sive_2024_09_17.yaml
└── wolf_2023_08_23.yaml

pyproject.toml
README.md
```

## Mapa de renomeação

| Ficheiro entregue | Ficheiro de destino | Ação |
|---|---|---|
| `1_expectations_gap_engine_FINAL.py` | `asymmetry_engine/expectations_gap_engine.py` | Renomear; sem alterar lógica funcional |
| `2_advanced_portfolio_risk_engine_FINAL.py` | `asymmetry_engine/portfolio_risk_engine.py` | Renomear; sem alterar lógica funcional |
| `3_debt_structure_gate_FINAL.py` | `asymmetry_engine/debt_structure_gate.py` | Renomear; sem alterar lógica funcional |
| `7_case_runner_FINAL.py` | `asymmetry_engine/case_runner.py` | Renomear e atualizar imports internos |
| `4_test_regression_FINAL.py` | `asymmetry_engine/tests/test_regression.py` | Renomear e substituir `importlib` por imports normais |
| `5_README_e_MATHEMATICAL_SPEC.md` | `README.md` e `docs/MATHEMATICAL_SPEC.md` | Opcional: dividir documentação |
| `6_Evidence_Registers_consolidados.md` | `docs/evidence_registers.md` | Renomear |

## Imports finais

No `case_runner.py`, substituir carregamentos ou referências de entrega por:

```python
from .expectations_gap_engine import (
    EngineConfig,
    ExpectationsGapEngine,
    FactoryData,
    FinancialInputs,
    RevenueQualityGate,
    Scenario,
    ValuationAssumptions,
)
from .debt_structure_gate import DebtStructure, maturity_wall_check
```

No ficheiro de testes:

```python
from asymmetry_engine.case_runner import run_full_case
from asymmetry_engine.debt_structure_gate import (
    ConvertibleInstrument,
    DebtStructure,
    maturity_wall_check,
)
from asymmetry_engine.expectations_gap_engine import (
    ExpectationsGapEngine,
    FactoryData,
    FinancialInputs,
    RevenueQualityGate,
    BacklogItem,
    Scenario,
    ValuationAssumptions,
)
```

Desta forma deixa de ser necessário `importlib.util`, `sys.modules` ou nomes especiais nos testes.

## `__init__.py` recomendado

```python
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
```

## `pyproject.toml` mínimo

```toml
[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[project]
name = "asymmetry-engine"
version = "4.1.0"
description = "Scenario valuation, capital-structure and liquidity research toolkit"
requires-python = ">=3.10"
dependencies = [
  "numpy>=1.24",
  "pandas>=2.0",
  "scipy>=1.10",
]

[project.optional-dependencies]
market-data = [
  "yfinance>=0.2",
  "arch>=6.0",
]
test = [
  "pytest>=7.0",
]

[tool.pytest.ini_options]
testpaths = ["asymmetry_engine/tests"]
addopts = "-ra"
```

## Instalação local

Na pasta-raiz do projeto:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[test,market-data]"
pytest -v
```

No Windows PowerShell, a ativação é:

```powershell
.venv\Scripts\Activate.ps1
```

## Sequência segura de migração

1. Criar a árvore de diretórios indicada.
2. Copiar os motores para os novos nomes, sem modificar conteúdo além dos imports internos.
3. Criar `asymmetry_engine/__init__.py`.
4. Atualizar os imports do `case_runner.py` para imports relativos.
5. Mover a suite de testes para `asymmetry_engine/tests/test_regression.py`.
6. Substituir o loader dinâmico por imports normais.
7. Criar e instalar o ambiente local com `pip install -e ".[test,market-data]"`.
8. Correr `pytest -v`.
9. Só depois acrescentar YAML, CLI, relatórios e fornecedores de dados.

## Critérios de aceitação

A migração está concluída quando:

- `from asymmetry_engine import run_full_case` funciona.
- `pytest -v` encontra e corre os testes sem `importlib`.
- O teste de assimetria dos cinco cenários continua em `1.3314597343477033` dentro da tolerância.
- O teste de Wolfspeed continua a produzir `CAPITAL_STRUCTURE_CRISIS_RISK` e gap de $1.2756 mil milhões.
- Não existem imports de nomes de ficheiro com prefixos numéricos.

## O que não fazer nesta etapa

- Não recalibrar thresholds.
- Não alterar fórmulas de valuation.
- Não substituir `yfinance` ainda.
- Não acrescentar CLI antes de os imports e testes estarem estáveis.
- Não misturar a migração estrutural com alterações de lógica, porque isso torna regressões difíceis de isolar.
