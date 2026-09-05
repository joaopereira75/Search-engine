# Asymmetry Engine v4.0.2-core

## README e especificação matemática

Motor de investigação, valuation e risco para micro/small-caps de hard-tech e semicondutores. Combina viabilidade física, valuation reversa, liquidez de microestrutura, deteção de financiamento tóxico e stress testing de portefólio.

Uso estritamente pessoal e não comercial.

## Estado atual

- `ExpectationsGapEngine`: reverse DCF, capacidade física, unit economics, diluição, WACC dinâmico e NOL sequencial.
- `AdvancedPortfolioRiskEngine`: GARCH, market impact não-linear, liquidity horizon, posições long/short, semi-covariância e VaR/ES.
- `debt_structure_gate`: maturity wall e overhang de convertíveis.
- Quatro casos reais: AEHR, POET, Sivers e Wolfspeed.
- Uma suite de regressão que cobre os bugs identificados.

## Dependências

```bash
pip install numpy pandas scipy yfinance arch pytest
```

## Estrutura dos ficheiros

```text
1_expectations_gap_engine_FINAL.py
2_advanced_portfolio_risk_engine_FINAL.py
3_debt_structure_gate_FINAL.py
4_test_regression.py
5_README_e_MATHEMATICAL_SPEC.md
6_Evidence_Registers_consolidados.md
```

## Quick start: Expectations Gap Engine

```python
from expectations_gap_engine_FINAL import (
    FinancialInputs,
    FactoryData,
    ValuationAssumptions,
    Scenario,
    run_expectations_gap_analysis,
)

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
    terminal_growth=0.02,
)

scenarios = [
    Scenario(
        name="Base",
        probability=1.0,
        revenue_cagr=0.20,
        ebit_margin=0.15,
        reinvestment_rate=0.30,
        exit_multiple=12.0,
    )
]

result = run_expectations_gap_analysis(
    financials=financials,
    factory=factory,
    valuation=valuation,
    scenarios=scenarios,
)

print(result["verdict"])
print(result["asymmetry"])
```

## Quick start: risco de portefólio

```python
from advanced_portfolio_risk_engine_FINAL import run_portfolio_stress_test

portfolio = {
    "AAPL": 400_000,
    "AMD": -150_000,
}

result = run_portfolio_stress_test(
    portfolio,
    confidence=0.95,
    impact_metric="H_95",
)

print(result["liquidation_var_usd"])
print(result["expected_shortfall_usd"])
```

## Quick start: maturity wall

```python
from debt_structure_gate_FINAL import (
    ConvertibleInstrument,
    DebtStructure,
    maturity_wall_check,
)

structure = DebtStructure(
    instruments=[
        ConvertibleInstrument(
            principal_usd=1_275_600_000,
            conversion_price_usd=63.0,
            maturity_date="2026-06-01",
            coupon_rate=0.0175,
        )
    ],
    straight_debt_usd=1_149_500_000,
)

result = maturity_wall_check(
    structure,
    current_price_usd=44.10,
    current_date="2023-08-23",
    cash_usd=2_954_900_000,
    annual_fcf_usd=-1_820_000_000,
    forecast_years=5,
)

print(result["verdict"])
print(result["total_refinancing_gap_usd"])
```

## Fórmulas

### Enterprise value

\[
EV = Market\ Cap + Total\ Debt - Cash
\]

### Capacidade física

Aplicável apenas quando `business_model == "fab"`.

\[
C_{saleable} = C_{installed} \times Utilization \times Yield
\]

A receita máxima física no ano \(t\) é:

\[
Revenue\ Ceiling_t = C_{saleable,t} \times ASP
\]

Para negócios classificados como `equipment_vendor` ou `not_applicable`, o motor devolve `NOT_APPLICABLE` em vez de construir um teto físico artificial. Nesse caso, pode avaliar concentração de cliente:

- `SEVERE_CONCENTRATION_RISK` se o maior cliente for superior a 50% da receita.
- `ELEVATED_CONCENTRATION_RISK` se o maior cliente estiver entre 30% e 50%.
- `DIVERSIFIED` se estiver em 30% ou menos.

### Curva-S de ramp

Para empresas pré-receita ou sem uma base de receitas útil, a rampa é modelada como logística:

\[
f(t) = \frac{1}{1 + e^{-k(t-m)}}
\]

A curva é normalizada para que a receita no último ano do horizonte seja igual à receita-alvo do cenário.

### WACC dinâmico

\[
WACC_t = WACC_{initial} + (WACC_{terminal} - WACC_{initial})
\times \min\left(\frac{t}{G}, 1\right)
\]

O fator de desconto cumulativo é:

\[
DF_t = \prod_{s=1}^{t}(1 + WACC_s)
\]

### NOLs e imposto

Para EBIT positivo, o escudo fiscal é limitado a 80% do EBIT anual:

\[
Shield_t = \min(EBIT_t, NOL_{balance}, 0.80 \times EBIT_t)
\]

\[
Taxable\ EBIT_t = EBIT_t - Shield_t
\]

As perdas operacionais aumentam o saldo de NOL; lucros consomem o NOL sequencialmente.

### FCFF

\[
NOPAT_t = EBIT_t - Tax_t
\]

\[
FCFF_t = NOPAT_t \times (1 - Reinvestment\ Rate_t)
\]

### Terminal value

O motor usa uma abordagem de múltiplo de saída:

\[
TV_N = EBIT_N \times Exit\ Multiple
\]

É sinalizado `TV_DOMINATED` se o valor presente do terminal exceder 80% do enterprise value calculado.

### Diluição

A lógica trata o capital levantado como uma emissão a fair value:

\[
Equity_{pre-dilution} = EV - Debt + Cash - Additional\ Debt + Dilution\ Capital
\]

\[
Equity_{current\ shareholders} = \max(0, Equity_{pre-dilution} - Dilution\ Capital)
\]

### Revenue Quality Gate

O backlog anunciado é descontado pela natureza contratual:

| Tipo | Desconto de reconhecimento | Fator reconhecível no curto prazo |
|---|---:|---:|
| `direct_po` | 20% | 80% |
| `nre_milestone` | 85% | 15% |
| `framework_calloff` | 80% | 20% |
| `product_committed` | 5% | 95% |

Classificação de qualidade:

- `PRODUCT_LED_BACKLOG`: pelo menos 50% do backlog é `direct_po` ou `product_committed`.
- `MIXED_QUALITY_BACKLOG`: pelo menos 20%, mas menos de 50%, é produto comprometido.
- `NRE_DOMINATED_BACKLOG`: menos de 20% é produto comprometido.

### Asymmetry ratio

\[
Asymmetry = \frac{\sum p_i \times \max(R_i, 0)}{\sum p_i \times |\min(R_i, 0)|}
\]

- `CORE_ELIGIBLE`: rácio igual ou superior a 2,5.
- `PILOT`: rácio igual ou superior a 1,5 e inferior a 2,5.
- `WATCHLIST_OR_REJECT`: rácio inferior a 1,5.

### GARCH(1,1)

\[
\sigma_t^2 = \omega + \alpha\epsilon_{t-1}^2 + \beta\sigma_{t-1}^2
\]

Se o ajuste GARCH falhar ou não houver dados suficientes, usa-se EWMA com \(\lambda = 0.94\); em séries muito curtas aplica-se um multiplicador conservador de 1,5 à volatilidade histórica.

### Market impact não-linear

\[
\phi = \frac{Q}{ADV_{down}}
\]

\[
MI_{base} = Y \times \sigma \times \sqrt{\phi}
\]

Para \(\phi > 0.02\):

\[
MI_{total} = MI_{base} \times e^{k(\phi - 0.02)}
\]

O impacto é limitado a 100% por razões numéricas.

\[
H_{95} = \frac{Spread}{2} + MI_{total} + Gap_{95}
\]

### Liquidity horizon, VaR e ES

\[
t_{liq} = \max\left(1, \frac{Q}{0.10 \times ADV_{down}}\right)
\]

A covariância é escalada pela raiz do horizonte de liquidação de cada posição. Para confiança \(c\):

\[
VaR = \sigma_p \times z_c \times Gross\ Exposure
\]

\[
ES = \sigma_p \times \frac{\varphi(z_c)}{1-c} \times Gross\ Exposure
\]

O custo agregado de impacto é somado ao VaR e ES.

### Maturity wall e convertíveis

\[
Conversion\ Ratio = \frac{Current\ Price}{Conversion\ Price}
\]

- `IN_THE_MONEY_ACTIVE_DILUTION_PRESSURE` se o rácio for igual ou superior a 1.
- `NEAR_THE_MONEY_WATCH` se estiver entre 0,85 e 1.
- `OUT_OF_THE_MONEY_NO_PRESSURE` se estiver abaixo de 0,85.

O caixa disponível na maturidade inclui explicitamente o FCF negativo:

\[
Projected\ Cash = \max(0, Cash + Annual\ FCF \times Years\ to\ Maturity)
\]

\[
Refinancing\ Gap = \max(0, Principal - Projected\ Cash)
\]

- `MATURITY_WALL_UNCOVERED` se o gap exceder 50% do principal.
- `MATURITY_WALL_PARTIAL_COVERAGE` se houver gap positivo inferior ou igual a 50%.
- `MATURITY_WALL_COVERED` se não houver gap.

## Execução dos testes

```bash
pytest 4_test_regression.py -v
```

## Limitações conhecidas

- Os thresholds são heurísticos e não foram calibrados estatisticamente; os quatro casos reais constituem validação qualitativa, não amostra suficiente para inferência estatística.
- O modelo não substitui verificação de filings, termos de dívida, contratos, cap table ou guidance da empresa.
- O módulo de liquidez depende de dados de mercado obtidos pelo `yfinance`, que pode ter limitações de cobertura, atraso ou qualidade.
- Os termos dos convertíveis da Wolfspeed usados no caso de teste eram aproximações e devem ser confirmados no filing correspondente antes de uma aplicação real.
- O modelo é uma ferramenta de investigação e de estruturação de cenários, não uma recomendação de investimento.
