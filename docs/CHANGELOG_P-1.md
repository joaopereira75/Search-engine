# CHANGELOG — Sessão de correção P-1 (fundação antes do P0)

## Bugs corrigidos e verificados (com teste, não por leitura)

### FIX 12 — `Scenario.dilution_usd` nunca teve efeito (prova algébrica)
`max(0, max(0, X+d) - d) ≡ max(0, X)` para todo `d ≥ 0`. Corrigido para
subtração única. **Regenerar TODOS os outputs** — POET muda materialmente
(-79.40% → -93.12% de expected return). Wolfspeed fica inalterado por
coincidência (Bull tem dilution=0).

### O "número de ouro" da regressão principal estava contaminado pelo bug
`1.3314597343477033` (citado em `test_regression.py`,
`test_engine_integration.py`, `8_asymmetry_engine_package_migration.md`)
foi gerado com o bug P-1 ativo, num teste cujos cenários usam
`dilution_usd != 0`. **Novo valor de referência correto:
`1.1693977033935576`** (verdict mantém-se `WATCHLIST_OR_REJECT`, decisão
qualitativa não muda). Atualizar as 3 ocorrências.

### FIX 13 — `capacity_max_units` era decorativo; agora é um teto real validado
`sustainable_max_capacity_units()` só lia `capacity_max_units` quando
`current_capacity_units is None` — nunca o caso em nenhum YAML real. Um
YAML podia declarar expansão que, somada à capacidade atual, ultrapassasse
o "máximo" declarado sem qualquer erro (confirmado: aceitava
current=800k + expansion=5M com capacity_max=1M, sem erro). Adicionada
validação: `current_capacity_units + expansion_capacity_units <= capacity_max_units`
(só para `business_model == "fab"`). Não quebra nenhum dos 4 YAMLs reais
(confirmado por execução).

### Teste de invariante económico vazio, corrigido
`test_higher_capacity_never_worsens_maximum_physical_gap` variava
`capacity_max_units` mantendo `current_capacity_units` fixo — como o campo
era morto, o gap ficava constante nos 5 pontos, e uma sequência constante
satisfaz trivialmente "está ordenada". Substituído por dois testes em
`corrected_capacity_tests.py`: um que varia `current_capacity_units` (o
campo que realmente importa, verificado a produzir gaps distintos e
ordenados) e um teste de **caracterização deliberada** que documenta que
`capacity_max_units` isolado não deve ter efeito quando
`current_capacity_units` fica fixo (para não voltar a confundir "teto de
validação" com "driver de cálculo" no futuro).

### FIX 16/17 — `estimate_scenario_funding_need()` estava órfão; agora ligado, mas documentadamente inerte
Estava implementado desde a v4.0.2 mas nunca era chamado por
`run()`/`case_runner.py` — invisível em todos os outputs reais. Ligado ao
`value_scenario()` (reaproveitando o `fcff_path` já calculado, corrigindo
também uma inconsistência de threshold entre `estimate_scenario_funding_need`
e `value_scenario` no critério CAGR-vs-curva-S) e propagado corretamente a
`case_runner._review_scenario_gates` como red flag (Base) / watch item
(outros) quando `dilution_usd` assumido pelo analista é inferior ao
funding gap implícito do modelo.

**Limitação documentada, não corrigida nesta sessão:** com a validação
atual (`0 < ebit_margin < 1`, nunca negativo), EBIT nunca é negativo, logo
FCFF nunca é negativo, logo este cross-check nunca dispara para nenhum
cenário válido hoje. Não relaxámos a validação porque a fórmula
`FCFF = NOPAT * (1 - reinvestment_rate)` fica economicamente invertida sob
NOPAT negativo (mais reinvestimento numa empresa deficitária reduz a
queima de caixa no modelo, o oposto da realidade). As duas correções têm
de ser feitas em conjunto — reservado para P0.

## Campos removidos (confirmado: não usados em nenhum código nem em nenhum YAML real)
`EngineConfig.min_incremental_roic_for_value_creation`,
`.strong_incremental_roic_spread`, `.revenue_search_low`,
`.revenue_search_high` + import não utilizado de `scipy.optimize.brentq`.

## Campos mantidos com aviso (presentes em YAMLs reais — remover quebraria casos existentes)
`FactoryData.incremental_capex_usd` e `Scenario.incremental_capex_usd`:
aceites, validados, mas emitem `RuntimeWarning` se != 0, porque ainda não
entram em nenhum cálculo de FCFF ou funding need. Modelação correta fica
reservada para P0 (decidir como interage com `reinvestment_rate` sem
duplicar contagem).

## Outras correções desta e da sessão anterior
- Encoding UTF-8 corrigido em todo `expectations_gap_engine.py`.
- Novo `EngineConfig.revenue_curve_floor_usd` (default $1M): abaixo deste
  nível de receita atual, usa-se sempre a curva-S física em vez de CAGR
  mecânico. Corrige o caso POET (receita ~$35k não devia ancorar CAGR
  composto de 5 anos). Aplicado consistentemente em `value_scenario()` e
  `estimate_scenario_funding_need()`.

## Verificação empírica final (perturbação dirigida, não genérica)
Todos os campos anteriormente listados como "precisa de mais teste" foram
re-testados com condições desenhadas para os ativar e confirmados **vivos**:
`qualification_lead_time_years`, `top_customer_revenue_pct`,
`wacc_glide_years`, `ramp_curve_steepness_factor`,
`physical_feasibility_tolerance`, `severe_implied_revenue_gap`,
`margin_implausibility_threshold_pp`, `terminal_value_dominance_warning_pct`,
`core_asymmetry_threshold`, `pilot_asymmetry_threshold`.
(Nota: 3 destes deram inicialmente "falso morto" por erros do meu próprio
harness de teste — documentado no processo, não escondido.)

## Ação necessária no teu repositório
1. Substituir `expectations_gap_engine.py` e `case_runner.py` pelas versões
   `_FIXED.py`.
2. Aplicar `corrected_capacity_tests.py` em
   `asymmetry_engine/tests/test_economic_invariants.py`.
3. Atualizar `1.3314597343477033` → `1.1693977033935576` em
   `test_regression.py`, `test_engine_integration.py`, e no critério de
   aceitação do documento de migração.
4. Substituir os 4 JSONs em `outputs/` pelos regenerados
   (`regenerated_case_outputs/`).
5. Correr `pytest -v` no teu ambiente real (não pude correr a tua suite
   completa, só recriei os módulos necessários num sandbox) para apanhar
   qualquer teste que eu não tenha conseguido antecipar.
