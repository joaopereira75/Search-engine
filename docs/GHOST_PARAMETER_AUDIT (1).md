# Ghost Parameter Audit — Asymmetry Engine

Data desta sessão. Método: (1) leitura estática (grep de `self.<field>` fora do
`validate()`), (2) perturbação empírica — cada campo mudado para um valor
extremo válido, motor completo corrido, JSON de output comparado byte-a-byte.

## P-1 — Bug crítico corrigido (prioridade sobre tudo, incluindo P0)

### `Scenario.dilution_usd` nunca teve efeito, para nenhum valor, em nenhum caso
Fórmula antiga:
```python
equity_value_pre_dilution = max(0, EV - debt + cash - additional_debt + dilution_usd)
equity_value_current_shareholders = max(0, equity_value_pre_dilution - dilution_usd)
```
Prova: `f(X, d) = max(0, max(0, X + d) - d) ≡ max(0, X)` para todo `d ≥ 0`
(verificado por casos: se `X+d≥0`, simplifica para `max(0,X)`; se `X+d<0`,
ambos os lados dão 0). Corrigido para subtração única. Impacto real:

| Caso | Antes (bug) | Depois (correto) |
|---|---:|---:|
| POET expected return | -79.40% | **-93.12%** |
| Wolfspeed expected return | -80.79% | -80.79% (inalterado — Bull tem dilution=0) |

## Novas descobertas desta sessão

### `FactoryData.capacity_max_units` é decorativo em todos os YAMLs reais
`sustainable_max_capacity_units()` só usa `capacity_max_units` quando
`current_capacity_units is None`. Os 4 casos reais definem sempre
`current_capacity_units`, logo `capacity_max_units` nunca entra no cálculo do
teto de receita — só serve para a validação `current ≤ max`.

### Teste de invariante económico vazio
`test_higher_capacity_never_worsens_maximum_physical_gap` varia
`capacity_max_units` (2B→5B) mantendo `current_capacity_units` fixo. Como o
campo é morto, o `physical_feasibility_gap` é **constante** nos 5 pontos
(`-0.10866962777121131` × 5). Uma sequência constante satisfaz trivialmente
`gaps == sorted(gaps, reverse=True)`. O teste não protege nada.
**Ação recomendada:** ou reescrever o teste para variar
`current_capacity_units` (o campo que de facto importa), ou decidir que
`capacity_max_units` deve passar a influenciar o teto (ex.: como limite de
longo prazo que a expansão pode aproximar-se) e implementar isso.

### Duplicação de nomes: `ValuationAssumptions.reinvestment_rate` / `.target_ebit_margin`
Nunca lidos pelo motor. Os campos homónimos em `Scenario` são os únicos
usados. Risco real de confusão para quem edita YAMLs.

### `estimate_scenario_funding_need()` / `survival_and_dilution()` são código órfão
Implementados, documentados como "FIX 9", mas nunca chamados por
`case_runner.py` nem `config.py`. Não aparecem em nenhum output JSON real.

## Confirmado morto (grep + perturbação concordam)
- `FinancialInputs.current_ebitda_usd`
- `FinancialInputs.current_invested_capital_usd`
- `FinancialInputs.current_shares` (só usado em validation.py para warning, não no motor)
- `FinancialInputs.share_price_usd` (idem)
- `FactoryData.incremental_capex_usd`
- `Scenario.incremental_capex_usd`
- `EngineConfig.min_incremental_roic_for_value_creation`
- `EngineConfig.strong_incremental_roic_spread`
- `EngineConfig.revenue_search_low` / `.revenue_search_high` (+ `brentq` importado, nunca chamado)

## Precisam de re-teste condicional antes de classificar (falsos negativos do meu harness)
- `FactoryData.qualification_lead_time_years`
- `FactoryData.top_customer_revenue_pct`
- `ValuationAssumptions.wacc_glide_years`
- `EngineConfig.ramp_curve_steepness_factor`
- `EngineConfig.physical_feasibility_tolerance`, `.severe_implied_revenue_gap`,
  `.core_asymmetry_threshold`, `.pilot_asymmetry_threshold`,
  `.terminal_value_dominance_warning_pct`, `.margin_implausibility_threshold_pp`

## Correções adicionais aplicadas nesta sessão (herdadas da revisão anterior)
- Encoding UTF-8 corrigido em `expectations_gap_engine.py` (mojibake em
  strings de erro/aviso em português).
- Threshold do ramp de receita: novo `EngineConfig.revenue_curve_floor_usd`
  (default $1M) — abaixo deste nível de receita atual, usa-se sempre a
  curva-S física em vez de CAGR mecânico. Corrige o caso POET (receita ~$35k
  não devia ancorar CAGR composto de 5 anos).
