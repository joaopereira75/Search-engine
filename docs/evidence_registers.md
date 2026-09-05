# Evidence Registers Consolidados — 4 Casos Reais

---

## CASO 1 — AEHR Test Systems (AEHR), point-in-time 2023-11-30

| Campo | Valor | Fonte | Confiana |
|---|---|---|---|
| Preo de fecho | $22,96 | 10-K FY2024, SEC EDGAR | Alta |
| Aes (interpoladas) | ~28.784.000 | 10-Q Q3 FY24 | Mdia |
| Market cap | $660.800.000 | Calculado | Mdia |
| Cash | $50.500.000 | Press release Q3 FY24 | Alta |
| Total debt | $0 (assumido) | Sem divulgao material | Baixa |
| Receita TTM | $63.000.000 (aprox.) | Entre FY23 e FY24 | Baixa/Mdia |
| NOL balance | $20.000.000 (placeholder) | No verificado para a data exata | Baixa |

**Gate motivado:** `business_model="equipment_vendor"` — AEHR no uma fab, fabricante de equipamento de teste. Teto de capacidade fsica neutralizado; `physical_feasibility_gap` devolve `NOT_APPLICABLE`.

**Resultado do motor:** `asymmetry_ratio ≈ 0,12-0,13`, veredito `WATCHLIST/REJECT` — direcionalmente consistente com o desempenho fraco da AEHR ao longo de 2024.

---

## CASO 2 — POET Technologies (POET), point-in-time 2024-05-15

| Campo | Valor | Fonte | Confiana |
|---|---|---|---|
| Preo de fecho | ~$2,20 | Yahoo Finance histrico | Mdia |
| Aes em circulao | 60.485.477 | Press release Q1 2024 | Alta |
| Market cap | $133.000.000 | Calculado | Mdia |
| Cash | $23.600.000 | Press release Q1 2024 | **Alta** |
| Receita | ~$35.000 (quase pr-receita) | Press release Q1 2024 | Alta |
| **NOL balance** | **$160.000.000** | 6-K, SEC EDGAR — citao direta | **Alta (corrigido de placeholder $80M)** |

**Gate motivado:** `business_model="not_applicable"` (estratgia fab-light) + `concentration_risk_check` com `top_customer_revenue_pct=0.90` → `SEVERE_CONCENTRATION_RISK`.

**Resultado do motor:** `asymmetry_ratio ≈ 2,01`, veredito `PILOT` — consistente com a subida de mais de 500% da POET ao longo de 2024.

**Teste de sensibilidade do NOL:** diferena de NOL entre $80M e $160M teve efeito prtico desprezvel (+0,36% no cenrio Bull) devido ao cap legal de 80% — o cap protege o motor de erros de input nesta varivel especfica.

---

## CASO 3 — Sivers Semiconductors (SIVE.ST), point-in-time 2024-09-17

| Campo | Valor | Fonte | Confiana |
|---|---|---|---|
| Preo de fecho | SEK 4,536 | Comunicado do Board (emisso ao CEO) | Alta |
| Aes em circulao | 235.884.460 | Reconstruo verificada | Alta |
| Market cap | SEK 1.070.091.900 | **Verificado independentemente (diferena 0,01%)** | Alta |
| Cash + linhas de crdito | ~SEK 78.000.000 | Relatrio Q1 2024 | Alta |
| Dvida (Formue Nord) | SEK 50.000.000, converso a SEK 4,86 | Comunicado maro 2024 | Alta |
| Receita LTM | SEK 240,531M | **Reconstruo verificada exata** | Alta |
| EBITDA LTM | -SEK 22,377M | **Reconstruo verificada exata** | Alta |
| EBIT LTM | -SEK 141,63M | **Reconstruo verificada exata** | Alta |

**Gate motivado:** `RevenueQualityGate` — backlog de SEK 101,4M anunciado como "quatro novos acordos importantes" decomposto por natureza contratual: 8,9M `direct_po`, 62,5M `nre_milestone`, 30M `framework_calloff`. Resultado: SEK 22,5M efetivamente reconhecvel no curto prazo — **desconto de 77,8%**, veredito `NRE_DOMINATED_BACKLOG`.

**Nota qualitativa:** preo de converso (SEK 4,86) estava acima do preo de mercado (SEK 4,536) nesta data — sem presso de converso ativa nesse momento especfico.

---

## CASO 4 — Wolfspeed, Inc. (WOLF), point-in-time 2023-08-23

| Campo | Valor | Fonte | Confiana |
|---|---|---|---|
| Preo de fecho (17/08/2023) | $44,10 | PR Newswire | Alta |
| Aes em circulao | ~124.794.000 | 10-K FY2023 | Alta |
| Market cap | ~$5.503.700.000 | Calculado | Mdia |
| Cash e investimentos | $2.954.900.000 | 10-K FY2023 | **Alta — valor exato** |
| Long-term debt | $1.149.500.000 | 10-K FY2023 (balano) | Alta |
| Convertible notes, net | $3.025.600.000 | 10-K FY2023 (balano) | Alta |
| **Dvida total** | **$4.175.100.000** | Soma | Alta |
| FCF Q4 FY23 | -$455.000.000 | Earnings call 16/08/2023 | Alta |
| Capacidade nameplate Mohawk Valley | $2.000M/ano a full utilization | Semiconductor Today | Mdia |
| **Termos exatos dos convertible notes** (preo converso, maturidade por srie) | Aproximados | **No confirmados linha a linha no 10-K** | **Baixa — pendente** |

**Desfecho real (fora do horizonte point-in-time, s para calibrao):** Chapter 11 em 30/06/2025; corte de ~$4,6 mil milhes de dvida; acionistas anteriores com apenas 3-5% do capital reorganizado.

**Gate hipotetizado e testado:** "Ramp Execution Burn Gate" (custo de subutilizao real de $35,6M/trimestre anualizado). Efeito no `asymmetry_ratio`: 0,222 → 0,210. **Rejeitado — efeito marginal, no construdo.**

**Gate motivado e construdo:** `maturity_wall_check` via `ConvertibleInstrument`/`DebtStructure`. Bug do `max(0,FCF)` encontrado e corrigido (escondia o burn de caixa real). Resultado final: `CAPITAL_STRUCTURE_CRISIS_RISK`, gap de refinanciamento de **$1.275.600.000** — reproduzido de forma independente em dois ambientes de execuo distintos, valor exato idntico.

---

## Resumo comparativo

| # | Caso | Arqu tipo | Gate | Veredito do motor | Consistncia com desfecho real |
|---|---|---|---|---|---|
| 1 | AEHR | Equipamento | `business_model` | REJECT | Consistente (ano difcil) |
| 2 | POET | Pr-receita/fab-light | `concentration_risk` | PILOT | Consistente (+500% em 2024) |
| 3 | Sivers | Hbrido NRE/produto | `RevenueQualityGate` | (aritmtica verificada; veredito no computado nesta sesso) | N/A |
| 4 | Wolfspeed | Fab real, crise capital | `maturity_wall_check` | REJECT (via debt bridge + physical check) | Consistente (Chapter 11 em 2025) |

**n=4. Continua a ser heurstica qualitativa informada por casos reais, no calibrao estatstica.**