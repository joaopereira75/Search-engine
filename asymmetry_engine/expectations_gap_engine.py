from __future__ import annotations

"""
Expectations Gap Engine — v4.1.0-core (encoding fix + ramp threshold fix)
====================================================================
Alterações nesta revisão face à v4.0.2-core:

  [FIX 10] Encoding corrigido: strings de erro/aviso em português estavam
           gravadas com mojibake (ex.: "invá¶¶lido" em vez de "inválido").
           Todas as strings deste ficheiro foram reescritas em UTF-8 limpo.
  [FIX 11] Threshold do ramp de receita: a curva-S física só era ignorada
           quando current_revenue_usd == 0. Isto fazia com que empresas
           quase pré-receita (ex.: POET, current_revenue ~ $35k) usassem
           CAGR mecânico em vez da curva-S, produzindo trajetórias sem
           qualquer disciplina física nos primeiros anos. Introduzido
           EngineConfig.revenue_curve_floor_usd (default $1,000,000):
           abaixo deste valor, a receita é tratada como insuficiente para
           ancorar um CAGR credível e usa-se sempre a curva-S logística.
           ATENÇÃO: este fix muda o valuation de casos com receita muito
           baixa e CAGR agressivo (ex.: POET Bull scenario).

9 fixes matemáticos + 2 gates evidence-based (herdados da v4.0.2):
  [FIX 1] Diluição fair-value: equity_atual = equity_pre - dilution_usd
  [FIX 2] Curva-S física de ramp (substitui rampa sintética de 1%)
  [FIX 3] WACC dinâmico (glide de risco de execução -> risco de setor)
  [FIX 4] FCFF sequencial com NOLs
  [FIX 5] Limite legal de 80% na utilização anual de NOLs (IRC 172(a))
  [FIX 6] Diagnóstico de dominância do Terminal Value
  [FIX 7] Validação física por cenário (não só no Reverse DCF)
  [FIX 8] Cross-check bottom-up da margem vs. unit economics
  [FIX 9] estimate_scenario_funding_need() -- funding derivado do burn
  [GATE A] business_model + NOT_APPLICABLE + concentration_risk_check
           (caso AEHR Test Systems, 2023-11-30)
  [GATE B] RevenueQualityGate integrado em value_scenario()
           (caso Sivers Semiconductors, 2024-09-17)
"""

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple
import math
import warnings

import numpy as np
import pandas as pd

# =============================================================================
# Data structures
# =============================================================================

@dataclass(frozen=True)
class FactoryData:
    capacity_max_units: float
    current_capacity_units: Optional[float] = None
    current_utilization: float = 0.80
    yield_rate: float = 0.90
    asp_usd: float = 0.0
    expansion_capacity_units: float = 0.0
    expansion_lead_time_years: float = 1.0
    qualification_lead_time_years: float = 0.5
    ramp_years: float = 1.0
    maintenance_capex_per_unit: float = 0.0
    variable_cost_per_unit: Optional[float] = None
    incremental_capex_usd: float = 0.0
    business_model: str = "fab"
    top_customer_revenue_pct: Optional[float] = None

    VALID_BUSINESS_MODELS = ("fab", "equipment_vendor", "not_applicable")

    def validate(self) -> None:
        numeric_fields = {
            "capacity_max_units": self.capacity_max_units,
            "current_capacity_units": self.current_capacity_units
            if self.current_capacity_units is not None else self.capacity_max_units,
            "asp_usd": self.asp_usd,
            "expansion_capacity_units": self.expansion_capacity_units,
            "expansion_lead_time_years": self.expansion_lead_time_years,
            "ramp_years": self.ramp_years,
            "qualification_lead_time_years": self.qualification_lead_time_years,
            "maintenance_capex_per_unit": self.maintenance_capex_per_unit,
            "incremental_capex_usd": self.incremental_capex_usd,
        }
        for name, value in numeric_fields.items():
            if value is None or not np.isfinite(value) or value < 0:
                raise ValueError(f"{name} deve ser finito e >= 0.")
        if not (0 < self.current_utilization <= 1):
            raise ValueError("current_utilization deve estar em (0, 1].")
        if not (0 < self.yield_rate <= 1):
            raise ValueError("yield_rate deve estar em (0, 1].")
        current_capacity = (
            self.current_capacity_units if self.current_capacity_units is not None else self.capacity_max_units
        )
        if current_capacity > self.capacity_max_units:
            raise ValueError("current_capacity_units não pode exceder capacity_max_units.")
        # [FIX 13] Antes desta validação, capacity_max_units não tinha
        # NENHUM papel económico: sustainable_max_capacity_units() nunca a
        # lê quando current_capacity_units está definido (o que acontece em
        # 100% dos casos reais). Um YAML podia declarar uma expansão que,
        # somada à capacidade atual, ultrapassasse largamente o "máximo"
        # declarado, sem qualquer erro. Esta validação torna
        # capacity_max_units um teto real e vinculativo sobre o total
        # instalado após expansão totalmente rampada (current + expansion),
        # medido em unidades brutas (antes de utilization/yield). Isto é
        # suficiente para garantir, por construção, que a receita saleable
        # nunca pode implicitamente exceder capacity_max_units, sem precisar
        # de um clamp redundante dentro do próprio cálculo do teto.
        if self.business_model == "fab":
            total_installed_after_expansion = current_capacity + self.expansion_capacity_units
            if total_installed_after_expansion > self.capacity_max_units + 1e-6:
                raise ValueError(
                    "current_capacity_units + expansion_capacity_units "
                    f"({total_installed_after_expansion:,.0f}) não pode exceder "
                    f"capacity_max_units ({self.capacity_max_units:,.0f}). "
                    "capacity_max_units deve representar o teto de longo prazo "
                    "após a expansão estar totalmente rampada."
                )
        if self.business_model not in self.VALID_BUSINESS_MODELS:
            raise ValueError(f"business_model deve ser um de {self.VALID_BUSINESS_MODELS}.")
        if self.top_customer_revenue_pct is not None and not (0 <= self.top_customer_revenue_pct <= 1):
            raise ValueError("top_customer_revenue_pct deve estar em [0, 1].")
        # [FIX 15] incremental_capex_usd é aceite e validado mas, tal como
        # Scenario.incremental_capex_usd, ainda não é incorporado em
        # nenhuma linha de FCFF ou funding need. Modelar isto corretamente
        # exige decidir como interage com reinvestment_rate (para não
        # duplicar contagem de capex), o que fica reservado para o motor de
        # funding/capital structure (P0). Um aviso explícito é preferível a
        # implementar uma fórmula apressada que pareça correta mas não seja.
        if self.incremental_capex_usd:
            warnings.warn(
                "FactoryData.incremental_capex_usd foi definido mas ainda NÃO "
                "é incorporado no FCFF nem no funding need. É aceite e "
                "validado, mas atualmente não tem qualquer efeito no "
                "valuation. Ver P0 do roadmap (funding/capital structure).",
                RuntimeWarning,
            )


@dataclass(frozen=True)
class FinancialInputs:
    market_cap_usd: float
    total_debt_usd: float = 0.0
    cash_usd: float = 0.0
    current_revenue_usd: float = 0.0
    current_ebitda_usd: Optional[float] = None
    current_invested_capital_usd: Optional[float] = None
    current_shares: Optional[float] = None
    share_price_usd: Optional[float] = None
    nol_balance_usd: float = 0.0

    def validate(self) -> None:
        for name, value in asdict(self).items():
            if value is None:
                continue
            if not np.isfinite(value):
                raise ValueError(f"{name} deve ser finito.")
        if self.market_cap_usd <= 0:
            raise ValueError("market_cap_usd deve ser > 0.")
        if self.total_debt_usd < 0 or self.cash_usd < 0:
            raise ValueError("Dívida e cash devem ser >= 0.")
        if self.current_revenue_usd < 0:
            raise ValueError("current_revenue_usd deve ser >= 0.")
        if self.nol_balance_usd < 0:
            raise ValueError("nol_balance_usd deve ser >= 0.")


@dataclass(frozen=True)
class ValuationAssumptions:
    wacc: float
    forecast_years: int = 5
    tax_rate: float = 0.21
    terminal_growth: float = 0.02
    target_ebit_margin: float = 0.20
    revenue_to_invested_capital: float = 1.5
    reinvestment_rate: float = 0.25
    wacc_initial: Optional[float] = None
    wacc_terminal: Optional[float] = None
    wacc_glide_years: Optional[int] = None

    def validate(self) -> None:
        if not (0 < self.wacc < 1):
            raise ValueError("WACC deve estar entre 0 e 1.")
        if self.forecast_years < 1 or self.forecast_years > 30:
            raise ValueError("forecast_years deve estar entre 1 e 30.")
        if not (0 <= self.tax_rate < 1):
            raise ValueError("tax_rate inválido.")
        if not (-0.05 < self.terminal_growth < self.wacc):
            raise ValueError("terminal_growth deve ser >= -5% e inferior ao WACC.")
        if not (0 < self.target_ebit_margin < 1):
            raise ValueError("target_ebit_margin inválida.")
        if not (0 <= self.reinvestment_rate <= 1):
            raise ValueError("reinvestment_rate inválido.")
        if self.wacc_initial is not None and not (0 < self.wacc_initial < 1):
            raise ValueError("wacc_initial deve estar entre 0 e 1.")
        if self.wacc_terminal is not None and not (0 < self.wacc_terminal < 1):
            raise ValueError("wacc_terminal deve estar entre 0 e 1.")

    @property
    def uses_dynamic_wacc(self) -> bool:
        return self.wacc_initial is not None and self.wacc_terminal is not None


@dataclass(frozen=True)
class Scenario:
    name: str
    probability: float
    revenue_cagr: float
    ebit_margin: float
    reinvestment_rate: float
    exit_multiple: float = 12.0
    incremental_capex_usd: float = 0.0
    additional_debt_usd: float = 0.0
    dilution_usd: float = 0.0

    def validate(self) -> None:
        if self.probability < 0:
            raise ValueError(f"{self.name}: probability inválida.")
        if not (-0.99 < self.revenue_cagr < 10):
            raise ValueError(f"{self.name}: revenue_cagr inválido.")
        if not (0 < self.ebit_margin < 1):
            raise ValueError(f"{self.name}: ebit_margin inválido.")
        if not (0 <= self.reinvestment_rate <= 1):
            raise ValueError(f"{self.name}: reinvestment_rate inválido.")
        if self.exit_multiple <= 0:
            raise ValueError(f"{self.name}: exit_multiple deve ser > 0.")
        if self.dilution_usd < 0:
            raise ValueError(f"{self.name}: dilution_usd inválido.")
        # [FIX 15] mesmo aviso que FactoryData.incremental_capex_usd — ver ali.
        if self.incremental_capex_usd:
            warnings.warn(
                f"{self.name}: Scenario.incremental_capex_usd foi definido mas "
                "ainda NÃO é incorporado no FCFF nem no funding need.",
                RuntimeWarning,
            )


@dataclass(frozen=True)
class BacklogItem:
    """[GATE B] Um item de backlog com natureza contratual explícita."""
    description: str
    amount_usd: float
    contract_type: str  # "direct_po" | "nre_milestone" | "framework_calloff" | "product_committed"

    RECOGNITION_DISCOUNT_DEFAULTS = {
        "direct_po": 0.80, "nre_milestone": 0.15,
        "framework_calloff": 0.20, "product_committed": 0.95,
    }

    def validate(self) -> None:
        if self.amount_usd < 0:
            raise ValueError(f"{self.description}: amount_usd deve ser >= 0.")
        if self.contract_type not in self.RECOGNITION_DISCOUNT_DEFAULTS:
            raise ValueError(f"{self.description}: contract_type inválido.")

    def near_term_recognizable(self, discount_override: Optional[float] = None) -> float:
        discount = discount_override if discount_override is not None else self.RECOGNITION_DISCOUNT_DEFAULTS[self.contract_type]
        return self.amount_usd * discount


@dataclass
class RevenueQualityGate:
    """[GATE B] Backlog anunciado -> receita efetiva pós-desconto contratual."""
    items: List[BacklogItem] = field(default_factory=list)

    def validate(self) -> None:
        for item in self.items:
            item.validate()

    def gross_announced_usd(self) -> float:
        return sum(i.amount_usd for i in self.items)

    def effective_near_term_usd(self) -> float:
        return sum(i.near_term_recognizable() for i in self.items)

    def verdict(self) -> str:
        gross = self.gross_announced_usd()
        if gross <= 0:
            return "NO_BACKLOG"
        product_pct = sum(
            i.amount_usd for i in self.items if i.contract_type in ("direct_po", "product_committed")
        ) / gross
        if product_pct >= 0.50:
            return "PRODUCT_LED_BACKLOG"
        elif product_pct >= 0.20:
            return "MIXED_QUALITY_BACKLOG"
        return "NRE_DOMINATED_BACKLOG"


@dataclass
class EngineConfig:
    physical_feasibility_tolerance: float = 0.05
    severe_implied_revenue_gap: float = 0.25
    # [FIX 14] Removidos: min_incremental_roic_for_value_creation,
    # strong_incremental_roic_spread, revenue_search_low, revenue_search_high.
    # Confirmado por grep + teste empírico que nunca eram lidos por nenhum
    # método do motor (o import de brentq que os acompanhava também nunca
    # era chamado). Nenhum YAML real (aehr/poet/sive/wolf) os definia
    # explicitamente em engine_config, logo a remoção não quebra nenhum
    # caso existente. Se a pesquisa de raiz for implementada no futuro
    # (reverse DCF real), estes campos devem voltar junto com o código que
    # os usa, não antes.
    dilution_warning_pct: float = 0.15
    dilution_reject_pct: float = 0.50
    core_asymmetry_threshold: float = 2.5
    pilot_asymmetry_threshold: float = 1.5
    ramp_curve_steepness_factor: float = 6.0
    nol_utilization_cap_pct: float = 0.80
    terminal_value_dominance_warning_pct: float = 0.80
    margin_implausibility_threshold_pp: float = 0.10
    # [FIX 11] Abaixo deste nível de receita atual, o CAGR mecânico deixa
    # de ser usado como base de projeção (mesmo que current_revenue > 0),
    # porque não existe base histórica suficiente para ancorar um CAGR
    # credível. Usa-se sempre a curva-S física de ramp nestes casos.
    revenue_curve_floor_usd: float = 1_000_000.0


# =============================================================================
# Engine
# =============================================================================

class ExpectationsGapEngine:
    VERSION = "EGE-4.2.0-core"

    def __init__(self, financials, factory, valuation, scenarios=None, config=None):
        self.financials = financials
        self.factory = factory
        self.valuation = valuation
        self.config = config or EngineConfig()
        self.scenarios = list(scenarios or self.default_scenarios())

        self.financials.validate()
        self.factory.validate()
        self.valuation.validate()
        for s in self.scenarios:
            s.validate()

        self.diagnostics: Dict[str, Any] = {"engine_version": self.VERSION, "warnings": [], "errors": []}
        if self.financials.current_revenue_usd <= 0:
            warnings.warn("current_revenue_usd não fornecida; usar-se-á curva-S física de ramp.", RuntimeWarning)

    @property
    def enterprise_value_usd(self) -> float:
        return self.financials.market_cap_usd + self.financials.total_debt_usd - self.financials.cash_usd

    def sustainable_max_capacity_units(self, years_from_now: float) -> float:
        years = max(0.0, float(years_from_now))
        current_installed = self.factory.current_capacity_units if self.factory.current_capacity_units is not None else self.factory.capacity_max_units
        current_saleable = current_installed * self.factory.current_utilization * self.factory.yield_rate
        expansion = self.factory.expansion_capacity_units
        if expansion <= 0:
            return float(current_saleable)
        start = self.factory.expansion_lead_time_years + self.factory.qualification_lead_time_years
        end = start + self.factory.ramp_years
        if years <= start:
            added = 0.0
        elif self.factory.ramp_years <= 0 or years >= end:
            added = expansion * self.factory.yield_rate
        else:
            added = expansion * self.factory.yield_rate * (years - start) / self.factory.ramp_years
        return float(max(0.0, current_saleable + added))

    def physical_revenue_ceiling(self, years_from_now: float, asp_usd: Optional[float] = None) -> float:
        asp = self.factory.asp_usd if asp_usd is None else float(asp_usd)
        return self.sustainable_max_capacity_units(years_from_now) * asp

    def _safe_logistic(self, x: np.ndarray) -> np.ndarray:
        return 1.0 / (1.0 + np.exp(-np.clip(x, -60.0, 60.0)))

    def _logistic_ramp_fraction(self, t: float) -> float:
        ramp_years = max(self.factory.ramp_years, 0.1)
        start = self.factory.expansion_lead_time_years + self.factory.qualification_lead_time_years
        midpoint = start + ramp_years / 2.0
        steepness = self.config.ramp_curve_steepness_factor / ramp_years
        return float(self._safe_logistic(np.array(steepness * (t - midpoint))))

    def _build_revenue_path(self, years: int, *, revenue_year_n: float, cagr: Optional[float] = None) -> List[float]:
        current_revenue = self.financials.current_revenue_usd
        # [FIX 11] CAGR mecânico só é usado quando existe uma base de
        # receita atual "credível" (acima do floor configurável). Abaixo
        # disso (ex.: empresas quase pré-receita como a POET, com receita
        # trimestral de dezenas de milhares de dólares), qualquer CAGR
        # composto ao longo de vários anos produz trajetórias arbitrárias
        # sem disciplina física — usa-se sempre a curva-S de ramp.
        revenue_floor = self.config.revenue_curve_floor_usd
        if current_revenue > revenue_floor and cagr is not None:
            return [current_revenue * ((1.0 + cagr) ** t) for t in range(1, years + 1)]
        raw = np.array([self._logistic_ramp_fraction(t) for t in range(1, years + 1)])
        raw = np.clip(raw, 1e-9, None)
        return list(revenue_year_n * (raw / raw[-1]))

    def _wacc_path(self, years: int) -> np.ndarray:
        va = self.valuation
        if not va.uses_dynamic_wacc:
            return np.full(years, va.wacc)
        glide = va.wacc_glide_years or years
        glide = max(1, min(glide, years))
        t = np.arange(1, years + 1)
        return va.wacc_initial + (va.wacc_terminal - va.wacc_initial) * np.clip(t / glide, 0.0, 1.0)

    def _cumulative_discount_factors(self, years: int) -> np.ndarray:
        return np.cumprod(1.0 + self._wacc_path(years))

    def _fcff_path_with_nol(self, revenue_path, ebit_margin, reinvestment_rate, nol_balance_usd=None):
        # [ACHADO P0-1 — NÃO CORRIGIDO NESTA SESSÃO, DE PROPÓSITO]
        # FCFF_t = NOPAT_t * (1 - reinvestment_rate) só é economicamente
        # correto quando NOPAT_t >= 0 (reinvestir uma fração de um lucro
        # reduz o FCFF distribuível). Quando NOPAT_t < 0, a mesma fórmula
        # ENCOLHE a queima de caixa à medida que reinvestment_rate sobe
        # (ex.: NOPAT=-100 -> FCFF=-10 com reinvestment_rate=0.9), o que é
        # o oposto do que acontece na realidade (mais capex/reinvestimento
        # numa empresa deficitária normalmente AUMENTA a queima de caixa,
        # não diminui). Isto está atualmente inofensivo/inatingível porque
        # Scenario.ebit_margin exige 0 < ebit_margin < 1 (nunca negativo),
        # logo NOPAT nunca é negativo através de um Scenario válido. NÃO
        # relaxar essa validação sem corrigir esta fórmula em conjunto —
        # fazer só uma das duas ativaria silenciosamente um cálculo de
        # queima de caixa invertido. Tratar como bloco único em P0.
        tax_rate = self.valuation.tax_rate
        cap_pct = self.config.nol_utilization_cap_pct
        nol = self.financials.nol_balance_usd if nol_balance_usd is None else nol_balance_usd
        fcff_path, ebit_path = [], []
        for revenue_t in revenue_path:
            ebit_t = revenue_t * ebit_margin
            ebit_path.append(ebit_t)
            if ebit_t > 0:
                shield = min(ebit_t, nol, cap_pct * ebit_t)
                taxable_ebit = ebit_t - shield
                nol -= shield
            else:
                taxable_ebit = 0.0
                nol += abs(ebit_t)
            tax = taxable_ebit * tax_rate
            nopat_t = ebit_t - tax
            fcff_path.append(nopat_t - nopat_t * reinvestment_rate)
        return fcff_path, ebit_path, nol

    def _incremental_roic_from_fcff_path(self, revenue_path, fcff_path, reinvestment_rate) -> Optional[float]:
        # [FIX 18] incremental_roic era um output fantasma: existia no schema
        # de sensitivity_engine.extract_snapshot() (base.get("incremental_roic")),
        # mas nenhum método do motor alguma vez escrevia essa chave — devolvia
        # sempre None, em todos os casos reais, sem aviso nenhum.
        #
        # PRIMEIRA VERSÃO DESTE FIX (histórico, corrigida antes de chegar a
        # produção): definir capital reinvestido como NOPAT_t * reinvestment_rate
        # produz um rácio matematicamente invariante à ebit_margin sempre que
        # o NOL não é o fator limitante — porque tanto o NOPAT incremental
        # como esse "reinvestimento" escalam linearmente com a margem e
        # cancelam-se na divisão. Confirmado empiricamente no caso Wolfspeed
        # (nol_balance_usd=0): incremental_roic ficava idêntico para
        # ebit_margin entre 5% e 25%. Isso não é o que "incremental ROIC"
        # deve significar — uma empresa com margem mais alta deve ser mais
        # eficiente em capital, não igualmente eficiente por definição.
        #
        # VERSÃO CORRIGIDA: usa ValuationAssumptions.revenue_to_invested_capital
        # (também um campo-fantasma até este fix) como rácio de rotação de
        # capital — a definição standard: Invested_Capital = Revenue / Turnover.
        #   ΔInvestedCapital = (Revenue_N - Revenue_1) / revenue_to_invested_capital
        #   NOPAT_t = fcff_t / (1 - reinvestment_rate)   [reinvestment_rate é
        #     escalar e constante ao longo do path, logo esta inversão é exata]
        #   incremental_roic = (NOPAT_N - NOPAT_1) / ΔInvestedCapital
        # Agora varia corretamente com a margem, porque o denominador depende
        # só de receita e rotação de capital, nunca de NOPAT.
        #
        # Devolve None quando não é computável: menos de 2 anos de forecast,
        # reinvestment_rate=100% (NOPAT indeterminado por esta via), ou
        # ΔInvestedCapital <= 0 (receita estagnada ou a cair — divisão sem
        # significado económico nesses casos, não um erro a esconder).
        if reinvestment_rate is None or reinvestment_rate >= 1.0:
            return None
        if len(fcff_path) < 2 or len(revenue_path) < 2:
            return None
        turnover = self.valuation.revenue_to_invested_capital
        if turnover is None or turnover <= 0:
            return None
        delta_invested_capital = (revenue_path[-1] - revenue_path[0]) / turnover
        if delta_invested_capital <= 1e-9:
            return None
        nopat_first = fcff_path[0] / (1.0 - reinvestment_rate)
        nopat_last = fcff_path[-1] / (1.0 - reinvestment_rate)
        roic = (nopat_last - nopat_first) / delta_invested_capital
        return float(roic) if np.isfinite(roic) else None

    def _terminal_value_exit_multiple(self, ebit_year_n: float, exit_multiple: float) -> float:
        return ebit_year_n * exit_multiple

    def physical_feasibility_gap(self, implied_revenue_year_n_usd, *, years_from_now=None, asp_usd=None):
        if self.factory.business_model != "fab":
            result = {
                "status": "NOT_APPLICABLE",
                "reason": f"business_model='{self.factory.business_model}': sem teto físico fiável.",
            }
            if self.factory.top_customer_revenue_pct is not None:
                pct = self.factory.top_customer_revenue_pct
                result["concentration_verdict"] = (
                    "SEVERE_CONCENTRATION_RISK" if pct > 0.50 else
                    "ELEVATED_CONCENTRATION_RISK" if pct > 0.30 else "DIVERSIFIED"
                )
                result["top_customer_revenue_pct"] = pct
            return result

        years = self.valuation.forecast_years if years_from_now is None else float(years_from_now)
        ceiling = self.physical_revenue_ceiling(years, asp_usd=asp_usd)
        implied = float(implied_revenue_year_n_usd)
        if implied <= 0:
            raise ValueError("implied_revenue_year_n_usd deve ser > 0.")
        gap = (implied - ceiling) / implied
        status = (
            "PHYSICALLY_UNSUPPORTED" if gap > self.config.severe_implied_revenue_gap else
            "STRETCHED" if gap > self.config.physical_feasibility_tolerance else "PHYSICALLY_FEASIBLE"
        )
        return {"status": status, "physical_feasibility_gap": float(gap), "physical_revenue_ceiling_usd": float(ceiling)}

    def bottom_up_margin_ceiling(self, asp_usd=None):
        asp = self.factory.asp_usd if asp_usd is None else asp_usd
        if asp <= 0:
            return {"status": "NOT_COMPUTABLE"}
        var_cost = self.factory.variable_cost_per_unit or 0.0
        maint = self.factory.maintenance_capex_per_unit or 0.0
        return {"status": "OK", "operating_margin_ceiling_pct": float((asp - var_cost - maint) / asp)}

    def margin_credibility_check(self, assumed_ebit_margin, asp_usd=None):
        ceiling = self.bottom_up_margin_ceiling(asp_usd=asp_usd)
        if ceiling.get("status") != "OK":
            return {"status": "NOT_COMPUTABLE"}
        gap = assumed_ebit_margin - ceiling["operating_margin_ceiling_pct"]
        threshold = self.config.margin_implausibility_threshold_pp
        verdict = "IMPLAUSIBLE_MARGIN" if gap > threshold else ("STRETCHED_MARGIN" if gap > 0 else "PHYSICALLY_CONSISTENT")
        return {"status": verdict, "margin_gap_pp": float(gap)}

    def survival_and_dilution(self, *, annual_fcf_burn_usd, years_to_inflection,
                                funding_required_usd=None, additional_debt_available_usd=0.0):
        gross_burn = funding_required_usd if funding_required_usd is not None else annual_fcf_burn_usd * years_to_inflection
        resources = self.financials.cash_usd + additional_debt_available_usd
        funding_gap = max(0.0, gross_burn - resources)
        dilution_pct = funding_gap / self.financials.market_cap_usd
        if funding_gap <= 0:
            verdict = "SURVIVES_TO_INFLECTION"
        elif dilution_pct <= self.config.dilution_warning_pct:
            verdict = "SURVIVES_WITH_MODERATE_DILUTION"
        elif dilution_pct <= self.config.dilution_reject_pct:
            verdict = "DILUTION_RISK_HIGH"
        else:
            verdict = "SURVIVAL_RISK"
        return {"status": verdict, "funding_gap_usd": float(funding_gap), "implied_dilution_pct": float(dilution_pct)}

    def estimate_scenario_funding_need(self, scenario, *, additional_debt_available_usd=0.0):
        # [FIX 16] Corrigido para usar o MESMO threshold (revenue_curve_floor_usd)
        # que value_scenario() usa para decidir CAGR vs curva-S. Antes desta
        # correção, esta função usava "current_revenue > 0" (o threshold
        # antigo, já removido de value_scenario), o que a fazia divergir
        # silenciosamente da trajetória de receita realmente usada no
        # valuation principal para o mesmo cenário — dois métodos da mesma
        # classe a assumir tetos de decisão diferentes para a mesma pergunta.
        years = self.valuation.forecast_years
        current_revenue = self.financials.current_revenue_usd
        revenue_year_n_target = max(current_revenue, 1.0) * ((1.0 + scenario.revenue_cagr) ** years)
        cagr_arg = scenario.revenue_cagr if current_revenue > self.config.revenue_curve_floor_usd else None
        revenue_path = self._build_revenue_path(years, revenue_year_n=revenue_year_n_target, cagr=cagr_arg)
        fcff_path, _, _ = self._fcff_path_with_nol(revenue_path, scenario.ebit_margin, scenario.reinvestment_rate)
        cumulative_burn = -sum(f for f in fcff_path if f < 0)
        survival = self.survival_and_dilution(
            annual_fcf_burn_usd=cumulative_burn, years_to_inflection=1.0,
            funding_required_usd=cumulative_burn, additional_debt_available_usd=additional_debt_available_usd,
        )
        return {"scenario": scenario.name, "cumulative_burn_usd": float(cumulative_burn),
                "suggested_dilution_usd": survival["funding_gap_usd"], "survival_status": survival["status"]}

    def value_scenario(self, scenario: Scenario, revenue_quality_gate: Optional[RevenueQualityGate] = None) -> Dict[str, Any]:
        years = self.valuation.forecast_years
        current_revenue = self.financials.current_revenue_usd
        revenue_year_n_target = max(current_revenue, 1.0) * ((1.0 + scenario.revenue_cagr) ** years)

        revenue_quality_check = None
        if revenue_quality_gate is not None:
            gross = revenue_quality_gate.gross_announced_usd()
            effective = revenue_quality_gate.effective_near_term_usd()
            verdict = revenue_quality_gate.verdict()
            revenue_quality_check = {
                "gross_backlog_usd": float(gross), "effective_backlog_usd": float(effective),
                "implied_discount_pct": float(1.0 - effective / gross) if gross > 0 else 0.0, "verdict": verdict,
            }
            if gross > 0:
                backlog_gap = gross - effective
                revenue_year_n_target = max(1.0, revenue_year_n_target - backlog_gap)
                revenue_quality_check["revenue_target_adjustment_usd"] = float(-backlog_gap)

        # [FIX 11] usa a mesma regra de floor que _build_revenue_path para
        # decidir se passa CAGR explícito ou deixa a curva-S dominar.
        cagr_arg = scenario.revenue_cagr if current_revenue > self.config.revenue_curve_floor_usd else None
        revenue_path = self._build_revenue_path(years, revenue_year_n=revenue_year_n_target, cagr=cagr_arg)
        fcff_path, ebit_path, nol_remaining = self._fcff_path_with_nol(revenue_path, scenario.ebit_margin, scenario.reinvestment_rate)
        discount_factors = self._cumulative_discount_factors(years)
        pv_explicit = float(np.sum(np.array(fcff_path) / discount_factors))
        revenue_n, ebit_n, fcff_n = revenue_path[-1], ebit_path[-1], fcff_path[-1]
        terminal_ev = self._terminal_value_exit_multiple(ebit_n, scenario.exit_multiple)
        pv_terminal = terminal_ev / discount_factors[-1]
        enterprise_value = pv_explicit + pv_terminal
        tv_pct_of_ev = pv_terminal / enterprise_value if enterprise_value > 0 else np.nan
        tv_dominance_flag = "TV_DOMINATED" if (np.isfinite(tv_pct_of_ev) and tv_pct_of_ev > self.config.terminal_value_dominance_warning_pct) else "OK"

        physical_check = self.physical_feasibility_gap(revenue_n, years_from_now=years)
        margin_check = self.margin_credibility_check(scenario.ebit_margin)
        incremental_roic = self._incremental_roic_from_fcff_path(revenue_path, fcff_path, scenario.reinvestment_rate)

        # [FIX 16] survival_and_dilution()/estimate_scenario_funding_need()
        # existiam desde a v4.0.2 mas nunca eram chamados por run()/
        # case_runner — código órfão, invisível em todos os outputs reais
        # (aehr/poet/sive/wolf). Agora estão ligados corretamente ao
        # pipeline (ver também case_runner._review_scenario_gates, FIX 17).
        # LIMITAÇÃO CONHECIDA E DELIBERADA: com a validação atual de
        # Scenario (0 < ebit_margin < 1), EBIT nunca pode ser negativo, logo
        # FCFF nunca é negativo (ver aviso em _fcff_path_with_nol), logo
        # cumulative_burn == 0 sempre e funding_check nunca dispara para
        # nenhum cenário válido hoje. Isto NÃO é um bug deste fix — é uma
        # ligação correta a uma funcionalidade que só passa a ter efeito
        # depois de P0 permitir ebit_margin negativo E corrigir o sinal do
        # FCFF sob NOPAT negativo em conjunto. Documentado para que ninguém
        # assuma, entretanto, que este cross-check já protege alguma coisa.
        cumulative_burn = -sum(f for f in fcff_path if f < 0)
        funding_check = self.survival_and_dilution(
            annual_fcf_burn_usd=cumulative_burn,
            years_to_inflection=1.0,
            funding_required_usd=cumulative_burn,
            additional_debt_available_usd=scenario.additional_debt_usd,
        )
        dilution_assumption_gap_usd = funding_check["funding_gap_usd"] - scenario.dilution_usd

        # [FIX 12] BUG CRÍTICO CORRIGIDO: a fórmula anterior somava
        # dilution_usd a equity_value_pre_dilution e depois subtraía
        # exatamente o mesmo valor. Algebricamente:
        #   f(X, d) = max(0, max(0, X + d) - d) == max(0, X)  para todo d >= 0
        # ou seja, dilution_usd nunca teve qualquer efeito no equity_value_usd
        # final, para nenhum valor, em nenhum caso alguma vez corrido.
        # equity_value_pre_dilution_usd deve representar o equity ANTES da
        # diluição (sem somar d); dilution_usd é subtraído uma única vez.
        equity_value_pre_dilution = max(0.0, enterprise_value - self.financials.total_debt_usd + self.financials.cash_usd
                                          - scenario.additional_debt_usd)
        equity_value_current_shareholders = max(0.0, equity_value_pre_dilution - scenario.dilution_usd)
        return_multiple = equity_value_current_shareholders / self.financials.market_cap_usd

        # [FIX 19] current_shares e share_price_usd eram campos-fantasma no
        # motor de valuation: aceites, validados, e usados apenas em
        # validation.py (cross-check market_cap_usd vs share_price_usd x
        # current_shares) — nunca em value_scenario(). Os 4 casos reais
        # (aehr/poet/sive/wolf) têm ambos os campos preenchidos no YAML, sem
        # qualquer uso no output do motor. Corrigido: implied_price_target_usd
        # = equity_value_current_shareholders / current_shares. Devolve None
        # quando current_shares não está definido ou não é positivo, em vez
        # de um ZeroDivisionError ou um número sem significado.
        #
        # implied_upside_vs_share_price_pct usa share_price_usd diretamente
        # (não é redundante com return_pct): return_pct compara contra
        # market_cap_usd, que pode divergir de share_price_usd x
        # current_shares em até 2% sem gerar HARD_FAIL (ver
        # _validate_economic_consistency). Quando essa divergência existe,
        # os dois retornos percentuais deixam de ser idênticos por
        # construção -- o que é informação real, não ruído.
        if self.financials.current_shares is not None and self.financials.current_shares > 0:
            implied_price_target_usd = equity_value_current_shareholders / self.financials.current_shares
        else:
            implied_price_target_usd = None

        if implied_price_target_usd is not None and self.financials.share_price_usd is not None \
                and self.financials.share_price_usd > 0:
            implied_upside_vs_share_price_pct = (implied_price_target_usd / self.financials.share_price_usd) - 1.0
        else:
            implied_upside_vs_share_price_pct = None

        red_flags = []
        if physical_check.get("status") == "PHYSICALLY_UNSUPPORTED":
            red_flags.append("scenario_exceeds_physical_capacity")
        if margin_check.get("status") == "IMPLAUSIBLE_MARGIN":
            red_flags.append("margin_not_supported_by_unit_economics")
        if tv_dominance_flag == "TV_DOMINATED":
            red_flags.append("terminal_value_dominates_thesis")
        if revenue_quality_check and revenue_quality_check["verdict"] == "NRE_DOMINATED_BACKLOG":
            red_flags.append("backlog_is_nre_dominated_not_committed_revenue")
        # [FIX 16] cross-check aditivo: só sinaliza quando o burn implícito
        # do próprio modelo é simultaneamente (a) classificado como risco
        # relevante pelo survival_and_dilution() e (b) maior do que o
        # dilution_usd que o analista assumiu manualmente. Não sinaliza s
        # se o gap for negativo (analista assumiu diluição prudente/superior
        # ao que o modelo implicaria).
        if funding_check.get("status") in ("DILUTION_RISK_HIGH", "SURVIVAL_RISK") and dilution_assumption_gap_usd > 0:
            red_flags.append("assumed_dilution_usd_may_understate_model_implied_funding_need")

        return {
            "scenario": scenario.name, "probability": float(scenario.probability),
            "revenue_year_n_usd": float(revenue_n), "ebit_year_n_usd": float(ebit_n), "fcff_year_n_usd": float(fcff_n),
            "nol_balance_remaining_usd": float(nol_remaining), "revenue_quality_check": revenue_quality_check,
            "enterprise_value_usd": float(enterprise_value), "equity_value_pre_dilution_usd": float(equity_value_pre_dilution),
            "equity_value_usd": float(equity_value_current_shareholders),
            "terminal_value_pct_of_ev": float(tv_pct_of_ev) if np.isfinite(tv_pct_of_ev) else np.nan,
            "tv_dominance_flag": tv_dominance_flag, "physical_feasibility": physical_check, "margin_credibility": margin_check,
            "incremental_roic": incremental_roic,
            "funding_check": funding_check, "dilution_assumption_gap_usd": float(dilution_assumption_gap_usd),
            "scenario_red_flags": red_flags, "return_multiple": float(return_multiple), "return_pct": float(return_multiple - 1.0),
            "implied_price_target_usd": float(implied_price_target_usd) if implied_price_target_usd is not None else None,
            "implied_upside_vs_share_price_pct": float(implied_upside_vs_share_price_pct)
                if implied_upside_vs_share_price_pct is not None else None,
        }

    def probability_weighted_value(self, scenario_results=None):
        results = list(scenario_results if scenario_results is not None else [self.value_scenario(s) for s in self.scenarios])
        probs = np.array([float(r["probability"]) for r in results])
        values = np.array([float(r["equity_value_usd"]) for r in results])
        probs = probs / probs.sum()
        expected_value = float(np.sum(probs * values))
        return {"expected_equity_value_usd": expected_value,
                "expected_return": expected_value / self.financials.market_cap_usd - 1.0, "scenario_results": results}

    def asymmetry_ratio(self, scenario_results, positive_threshold=0.0):
        current = self.financials.market_cap_usd
        positive, negative = [], []
        for r in scenario_results:
            p, ret = float(r["probability"]), float(r["equity_value_usd"]) / current - 1.0
            (positive if ret > positive_threshold else negative).append(p * (ret if ret > 0 else abs(ret)))
        reward, risk = float(sum(positive)), float(sum(negative))
        ratio = np.inf if risk == 0 and reward > 0 else (reward / risk if risk > 0 else 0.0)
        verdict = "CORE_ELIGIBLE" if ratio >= self.config.core_asymmetry_threshold else ("PILOT" if ratio >= self.config.pilot_asymmetry_threshold else "WATCHLIST_OR_REJECT")
        return {"asymmetry_ratio": float(ratio), "verdict": verdict}

    def default_scenarios(self) -> List[Scenario]:
        return [
            Scenario(name="Bear", probability=0.25, revenue_cagr=-0.10, ebit_margin=0.05, reinvestment_rate=0.40, exit_multiple=8.0),
            Scenario(name="Base", probability=0.50, revenue_cagr=0.20, ebit_margin=0.15, reinvestment_rate=0.30, exit_multiple=12.0),
            Scenario(name="Bull", probability=0.25, revenue_cagr=0.40, ebit_margin=0.25, reinvestment_rate=0.20, exit_multiple=18.0),
        ]

    def run(self, revenue_quality_gates: Optional[Dict[str, RevenueQualityGate]] = None) -> Dict[str, Any]:
        gates = revenue_quality_gates or {}
        scenario_results = [self.value_scenario(s, revenue_quality_gate=gates.get(s.name)) for s in self.scenarios]
        pwv = self.probability_weighted_value(scenario_results)
        asym = self.asymmetry_ratio(scenario_results)
        red_flags = []
        if any("scenario_exceeds_physical_capacity" in sr.get("scenario_red_flags", []) for sr in scenario_results):
            red_flags.append("one_or_more_scenarios_exceed_physical_capacity")
        if any("backlog_is_nre_dominated_not_committed_revenue" in sr.get("scenario_red_flags", []) for sr in scenario_results):
            red_flags.append("backlog_quality_concern")
        if asym["verdict"] == "WATCHLIST_OR_REJECT":
            red_flags.append("insufficient_asymmetry")
        verdict = "WATCHLIST / REJECT" if red_flags else ("CORE CANDIDATE" if asym["verdict"] == "CORE_ELIGIBLE" else "PILOT CANDIDATE")
        return {
            "engine_version": self.VERSION, "status": "OK",
            "market": {"market_cap_usd": self.financials.market_cap_usd, "enterprise_value_usd": self.enterprise_value_usd},
            "scenario_valuation": scenario_results, "probability_weighted_valuation": pwv,
            "asymmetry": asym, "red_flags": red_flags, "verdict": verdict,
        }


def run_expectations_gap_analysis(*, financials, factory, valuation, scenarios=None, config=None,
                                     revenue_quality_gates=None) -> Dict[str, Any]:
    engine = ExpectationsGapEngine(financials=financials, factory=factory, valuation=valuation,
                                     scenarios=scenarios, config=config)
    return engine.run(revenue_quality_gates=revenue_quality_gates)


if __name__ == "__main__":
    financials = FinancialInputs(market_cap_usd=500_000_000, total_debt_usd=50_000_000, cash_usd=40_000_000,
                                   current_revenue_usd=100_000_000, current_shares=120_000_000, nol_balance_usd=85_000_000)
    factory = FactoryData(capacity_max_units=2_000_000, current_capacity_units=1_200_000, current_utilization=0.80,
                            yield_rate=0.90, asp_usd=250.0, expansion_capacity_units=800_000,
                            expansion_lead_time_years=1.0, ramp_years=1.0, qualification_lead_time_years=0.5,
                            variable_cost_per_unit=150.0)
    valuation = ValuationAssumptions(wacc=0.12, wacc_initial=0.20, wacc_terminal=0.10, forecast_years=5,
                                       tax_rate=0.21, terminal_growth=0.02, target_ebit_margin=0.20, reinvestment_rate=0.25)
    scenarios = [
        Scenario(name="Death / Severe Delay", probability=0.20, revenue_cagr=-0.10, ebit_margin=0.03,
                  reinvestment_rate=0.50, exit_multiple=6.0, dilution_usd=150_000_000),
        Scenario(name="Delayed Ramp", probability=0.25, revenue_cagr=0.05, ebit_margin=0.10,
                  reinvestment_rate=0.40, exit_multiple=9.0, dilution_usd=80_000_000),
        Scenario(name="Base Inflection", probability=0.35, revenue_cagr=0.25, ebit_margin=0.18,
                  reinvestment_rate=0.30, exit_multiple=13.0, dilution_usd=40_000_000),
        Scenario(name="Strong Re-rating", probability=0.15, revenue_cagr=0.40, ebit_margin=0.25,
                  reinvestment_rate=0.20, exit_multiple=18.0),
        Scenario(name="Full Bottleneck", probability=0.05, revenue_cagr=0.60, ebit_margin=0.30,
                  reinvestment_rate=0.15, exit_multiple=22.0),
    ]
    result = run_expectations_gap_analysis(financials=financials, factory=factory, valuation=valuation, scenarios=scenarios)
    print("Verdict:", result["verdict"])
    print("Asymmetry ratio:", result["asymmetry"]["asymmetry_ratio"])
