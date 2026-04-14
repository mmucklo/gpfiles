/**
 * term_glossary.ts — Canonical domain term definitions for the Alpha Control Center.
 *
 * Every arcane term rendered in the UI should use <TermLabel term="KEY" /> so
 * the user can hover for a tooltip and click for a full drill-down.
 *
 * Rules:
 *   - short: ≤ 120 chars — fits comfortably in a tooltip
 *   - long:  Markdown ok — rendered in drill-down popover
 *   - formula: computation / threshold details
 *   - source: data origin (feed, symbol, TTL)
 *   - trading_impact: required for regime labels — how it shifts signals
 *   - related: other GLOSSARY keys linked from drill-down
 */

export type GlossaryEntry = {
  term: string;           // canonical key, e.g. "CORRELATION_REGIME"
  display: string;        // label shown in UI
  short: string;          // 1-sentence tooltip (~120 chars)
  long: string;           // multi-paragraph drill-down (markdown ok)
  formula?: string;       // computation / threshold details
  source?: string;        // data source
  trading_impact?: string; // how it shifts signals / sizing
  related?: string[];     // other glossary keys
  phase_note?: string;    // e.g. "Added in Phase 14 — values not yet shown"
};

export const GLOSSARY: Record<string, GlossaryEntry> = {

  // ── Section / card titles ────────────────────────────────────────────────

  CORRELATION_REGIME: {
    term: "CORRELATION_REGIME",
    display: "Correlation Regime",
    short: "TSLA vs QQQ 5-day rolling correlation z-score — measures whether TSLA trades with the market or independently.",
    long: `**Correlation Regime** classifies whether TSLA is trading in sync with the broad market (QQQ) or moving on its own story.

It is computed every hour from 5-day rolling close-to-close returns for TSLA, QQQ, and the Mag7 basket. The regime label is determined by the z-score of the TSLA↔QQQ correlation relative to its 60-day history.

**Labels:**
- **IDIOSYNCRATIC** (z < −2): TSLA is decoupled. Company-specific factors dominate. Amplifies SENTIMENT and CONTRARIAN models.
- **MACRO_LOCKED** (z > +2): TSLA is highly correlated. Macro forces dominate. Amplifies MACRO model.
- **NORMAL**: No regime adjustment applied.`,
    formula: "z = (corr_5d − mean_60d) / std_60d",
    source: "yfinance · TSLA + QQQ + AAPL/MSFT/GOOGL/AMZN/META/NVDA/TSLA · 5-day window · 1h TTL cache",
    trading_impact: "IDIOSYNCRATIC → SENTIMENT/CONTRARIAN confidence ×1.20, MACRO ×1.00. MACRO_LOCKED → MACRO confidence ×1.20.",
    related: ["IDIOSYNCRATIC", "MACRO_LOCKED", "NORMAL"],
  },

  MACRO_REGIME: {
    term: "MACRO_REGIME",
    display: "Macro Regime",
    short: "Overall market risk appetite — RISK_ON, RISK_OFF, or NEUTRAL — derived from VIX and SPY trend.",
    long: `**Macro Regime** classifies the current market risk environment.

It is derived from VIX level and SPY's 5-day trend direction. The regime feeds into the regime-Kelly multiplier that scales position sizing up or down.`,
    formula: "RISK_OFF if VIX > 25 or SPY 5d trend < −1%; RISK_ON if VIX < 15 and SPY 5d trend > +1%; else NEUTRAL",
    source: "VIX spot via yfinance (^VIX) · SPY via yfinance · refreshed with intelligence poll",
    trading_impact: "RISK_OFF → regime_multiplier = 0.5 (halves Kelly sizing). RISK_ON → regime_multiplier = 1.0.",
    related: ["RISK_ON", "RISK_OFF", "NEUTRAL", "REGIME_KELLY_MULTIPLIER", "FRACTIONAL_KELLY"],
  },

  PRE_MARKET_BIAS: {
    term: "PRE_MARKET_BIAS",
    display: "Pre-Market Bias",
    short: "Composite directional bias from Asia/Europe/US-futures before US open — scores −1 (bearish) to +1 (bullish).",
    long: `**Pre-Market Bias** aggregates overnight index moves into a single directional score before the US cash session opens.

**Weights:**
- Asia 30%: N225, HSI, SSE
- Europe 40%: STOXX50E, DAX, FTSE
- US Futures 30%: ES (S&P 500), NQ (Nasdaq)

FX overrides are applied: a strong USD (DXY +0.5%) dampens the bullish contribution.`,
    formula: "bias = Σ(weight × index_change_pct) / Σ(weight), clamped to [−1, +1]",
    source: "yfinance · ^N225 ^HSI 000001.SS ^STOXX50E ^GDAXI ^FTSE ^GSPC=F ^NDX=F DX-Y.NYB · refreshed pre-market",
    trading_impact: "bias > 0.3 adds +0.05 to SENTIMENT/MACRO confidence; bias < −0.3 subtracts 0.05.",
    related: ["ASIA_WEIGHT", "EUROPE_WEIGHT", "US_FUTURES_WEIGHT", "FX_OVERRIDE"],
  },

  INSTITUTIONAL_FLOW: {
    term: "INSTITUTIONAL_FLOW",
    display: "Institutional Flow",
    short: "Put/Call ratio and total open interest on TSLA options — signals institutional positioning.",
    long: `**Institutional Flow** tracks the put/call ratio and total open interest on TSLA options.

A low P/C ratio (< 0.7) suggests calls dominating — institutions lean bullish. A high P/C (> 1.3) suggests put hedging — bearish lean.`,
    formula: "pc_ratio = total_put_oi / total_call_oi",
    source: "yfinance options chain · OI ≥ 100 filter · 60s TTL cache",
    trading_impact: "pc_ratio < 0.7 → OPTIONS_FLOW signal bullish boost. pc_ratio > 1.3 → bearish signal boost.",
    related: ["OPTIONS_FLOW"],
  },

  EV_SECTOR: {
    term: "EV_SECTOR",
    display: "EV Sector",
    short: "TSLA's performance vs. EV peer basket (NIO, RIVN, LCID) — detects sector-wide moves vs. TSLA-specific moves.",
    long: `**EV Sector** compares TSLA's intraday return against a basket of EV peers.

When TSLA moves with the EV sector, the signal is sector-driven (macro-ish). When TSLA diverges significantly, the move is idiosyncratic.`,
    source: "yfinance · TSLA NIO RIVN LCID · intraday returns · refreshed with intelligence poll",
    trading_impact: "Strong EV sector alignment reduces CONTRARIAN signal weight; divergence amplifies it.",
    related: ["EV_SECTOR", "IDIOSYNCRATIC"],
  },

  CONGRESS_DISCLOSURE: {
    term: "CONGRESS_DISCLOSURE",
    display: "Congress STOCK Act Disclosure",
    short: "TSLA trades by Congress members filed under the STOCK Act within 48h — committee-weighted for significance.",
    long: `**Congress STOCK Act Disclosure** ingests congressional equity trade filings for TSLA.

Trades filed within 48h receive the raw signal. Committee members on Senate Commerce or House Energy & Commerce receive a 2× weight multiplier because of their oversight of EV/tech sectors.

**Signal effect:**
- Net buying (committee-weighted) → SENTIMENT confidence ×1.15
- Net selling → SENTIMENT confidence ×0.85`,
    source: "House eFD XML disclosure feed (efts.house.gov) · Senate STOCK Act filings · 48h lag filter",
    trading_impact: "Bullish congress signal → SENTIMENT confidence ×1.15. Bearish → ×0.85.",
    related: ["SENTIMENT"],
  },

  CATALYST_TRACKER: {
    term: "CATALYST_TRACKER",
    display: "Catalyst Tracker",
    short: "Upcoming TSLA earnings date and days until — gates signal aggressiveness near binary events.",
    long: `**Catalyst Tracker** monitors TSLA's earnings calendar to prevent entering positions just before binary events.

As earnings approach, signal confidence thresholds are raised or positions are avoided entirely to prevent gamma-risk blow-ups from the earnings volatility crush.`,
    source: "yfinance calendar API · refreshed daily",
    trading_impact: "Days ≤ 2 before earnings: BULLISH threshold raised to 0.92 (normally 0.80). Alerts shown in UI.",
    related: ["FRACTIONAL_KELLY"],
  },

  // ── Regime labels ─────────────────────────────────────────────────────────

  IDIOSYNCRATIC: {
    term: "IDIOSYNCRATIC",
    display: "IDIOSYNCRATIC",
    short: "TSLA is decoupled from QQQ (z-score < −2) — company-specific story dominates. SENTIMENT/CONTRARIAN amplified.",
    long: `**IDIOSYNCRATIC** is a Correlation Regime label.

It fires when the z-score of the TSLA↔QQQ 5-day rolling correlation drops below −2 standard deviations from its 60-day mean. This means TSLA is moving on its own story rather than tracking the broad market.

During IDIOSYNCRATIC regimes:
- SENTIMENT model confidence is multiplied by ×1.20
- CONTRARIAN model confidence is multiplied by ×1.20
- MACRO model is unchanged (no amplification — macro forces less relevant)`,
    formula: "z_score < −2.0",
    source: "yfinance · 5-day rolling correlation of TSLA and QQQ closes · 60d z-score normalization",
    trading_impact: "SENTIMENT confidence ×1.20, CONTRARIAN confidence ×1.20, MACRO unchanged.",
    related: ["CORRELATION_REGIME", "MACRO_LOCKED", "NORMAL", "SENTIMENT", "CONTRARIAN"],
  },

  MACRO_LOCKED: {
    term: "MACRO_LOCKED",
    display: "MACRO_LOCKED",
    short: "TSLA strongly correlated with QQQ (z-score > +2) — broad market moves dominate. MACRO model amplified.",
    long: `**MACRO_LOCKED** is a Correlation Regime label.

It fires when the z-score of the TSLA↔QQQ 5-day rolling correlation exceeds +2 standard deviations. TSLA is moving with the market, so macro factors are the primary driver.

During MACRO_LOCKED regimes:
- MACRO model confidence is multiplied by ×1.20
- SENTIMENT and CONTRARIAN models are unchanged`,
    formula: "z_score > +2.0",
    source: "yfinance · 5-day rolling correlation of TSLA and QQQ closes · 60d z-score normalization",
    trading_impact: "MACRO confidence ×1.20, SENTIMENT/CONTRARIAN unchanged.",
    related: ["CORRELATION_REGIME", "IDIOSYNCRATIC", "NORMAL", "MACRO"],
  },

  NORMAL: {
    term: "NORMAL",
    display: "NORMAL",
    short: "Correlation regime z-score between −2 and +2 — no regime-based confidence amplification applied.",
    long: `**NORMAL** is the default Correlation Regime label.

No amplification multipliers are applied to model confidence when the regime is NORMAL. All models operate at their base confidence scores.`,
    formula: "−2.0 ≤ z_score ≤ +2.0",
    source: "yfinance · 5-day rolling correlation of TSLA and QQQ closes",
    trading_impact: "No confidence multiplier adjustments.",
    related: ["CORRELATION_REGIME", "IDIOSYNCRATIC", "MACRO_LOCKED"],
  },

  RISK_ON: {
    term: "RISK_ON",
    display: "RISK_ON",
    short: "Low-fear macro environment (VIX < 15, SPY trending up) — full Kelly sizing applied.",
    long: `**RISK_ON** is a Macro Regime label indicating a benign market environment.

Characterized by VIX below 15 and SPY on an uptrend over the past 5 days. In this regime the full Kelly fraction is applied to sizing (regime_multiplier = 1.0).`,
    formula: "VIX < 15 AND SPY 5d trend > +1%",
    source: "^VIX and SPY via yfinance",
    trading_impact: "regime_multiplier = 1.0 → full Kelly sizing.",
    related: ["MACRO_REGIME", "RISK_OFF", "NEUTRAL", "REGIME_KELLY_MULTIPLIER"],
  },

  RISK_OFF: {
    term: "RISK_OFF",
    display: "RISK_OFF",
    short: "High-fear macro environment (VIX > 25 or SPY in downtrend) — Kelly sizing halved.",
    long: `**RISK_OFF** is a Macro Regime label indicating elevated market stress.

Triggered by VIX above 25 or SPY in a downtrend over the past 5 days. Position sizing is halved to preserve capital during volatile conditions.`,
    formula: "VIX > 25 OR SPY 5d trend < −1%",
    source: "^VIX and SPY via yfinance",
    trading_impact: "regime_multiplier = 0.5 → Kelly sizing halved.",
    related: ["MACRO_REGIME", "RISK_ON", "NEUTRAL", "REGIME_KELLY_MULTIPLIER"],
  },

  NEUTRAL: {
    term: "NEUTRAL",
    display: "NEUTRAL",
    short: "Macro regime between RISK_ON and RISK_OFF — standard Kelly sizing applies.",
    long: `**NEUTRAL** is the default Macro Regime label when neither RISK_ON nor RISK_OFF conditions are met.

Standard Kelly sizing applies (regime_multiplier = 1.0 or a moderate value depending on VIX tier).`,
    formula: "15 ≤ VIX ≤ 25 AND −1% ≤ SPY 5d trend ≤ +1%",
    source: "^VIX and SPY via yfinance",
    trading_impact: "regime_multiplier = 1.0 (or VIX-tier based).",
    related: ["MACRO_REGIME", "RISK_ON", "RISK_OFF"],
  },

  // ── Model types ───────────────────────────────────────────────────────────

  SENTIMENT: {
    term: "SENTIMENT",
    display: "SENTIMENT",
    short: "News NLP model: bullish/bearish confidence from recent TSLA headlines and Musk mentions.",
    long: `**SENTIMENT** is an Alpha Engine signal model that scores the tone of recent TSLA news.

NLP-based sentiment analysis is applied to headlines from the past 24h that mention TSLA, Tesla, or Elon Musk. Scores are normalized to [−1, +1] and mapped to signal confidence.

Amplified during IDIOSYNCRATIC correlation regime (×1.20) and by congressional TSLA buying (×1.15).`,
    source: "yfinance news API · TSLA headlines · refreshed with intelligence poll",
    trading_impact: "High positive sentiment → BULLISH conviction signal at confidence 0.80–0.95.",
    related: ["CONTRARIAN", "CONGRESS_DISCLOSURE", "IDIOSYNCRATIC"],
  },

  OPTIONS_FLOW: {
    term: "OPTIONS_FLOW",
    display: "OPTIONS_FLOW",
    short: "Put/call ratio and OI model: low P/C ratio signals institutional call accumulation (bullish).",
    long: `**OPTIONS_FLOW** is an Alpha Engine signal model based on TSLA options open interest.

A put/call ratio below 0.7 suggests call accumulation — institutions leaning bullish. Above 1.3 suggests put hedging. The model converts this to a directional signal.`,
    source: "yfinance options chain · strike OI ≥ 100 filter · 60s TTL cache",
    trading_impact: "P/C < 0.7 → BULLISH OPTIONS_FLOW signal. P/C > 1.3 → bearish.",
    related: ["INSTITUTIONAL_FLOW"],
  },

  MACRO: {
    term: "MACRO",
    display: "MACRO",
    short: "Macro conditions model: VIX level, SPY trend, and earnings proximity combined into a regime signal.",
    long: `**MACRO** is an Alpha Engine signal model that evaluates broad market conditions.

It combines VIX level, SPY trend, and earnings proximity to produce a conviction score. Amplified during MACRO_LOCKED correlation regime (×1.20).`,
    source: "^VIX, SPY via yfinance · earnings calendar",
    trading_impact: "Amplified during MACRO_LOCKED regime (×1.20). Reduced by RISK_OFF (×0.5 sizing).",
    related: ["MACRO_LOCKED", "MACRO_REGIME", "RISK_OFF"],
  },

  VOLATILITY: {
    term: "VOLATILITY",
    display: "VOLATILITY",
    short: "IV vs. realized vol model: signals when implied volatility is mispriced relative to historical vol.",
    long: `**VOLATILITY** is an Alpha Engine signal model based on implied vs. realized volatility.

When IV is significantly below realized volatility, options are cheap — a buying opportunity. The vol_ratio (realized/implied) feeds into Kelly sizing.`,
    formula: "vol_ratio = realized_vol_20d / implied_vol",
    source: "yfinance historical closes (20d) · live options chain IV",
    trading_impact: "vol_ratio feeds Kelly final_multiplier: min(1, vol_ratio) × kelly_base.",
    related: ["FRACTIONAL_KELLY", "VIX_MULTIPLIER"],
  },

  CONTRARIAN: {
    term: "CONTRARIAN",
    display: "CONTRARIAN",
    short: "Mean-reversion model: fires when TSLA is extended from its moving average with extreme sentiment.",
    long: `**CONTRARIAN** is an Alpha Engine signal model looking for overextended price action.

It fires when TSLA is statistically extended from its 20-day moving average (> 2σ) and sentiment is already extremely one-sided — a mean-reversion setup.

Amplified during IDIOSYNCRATIC correlation regime (×1.20).`,
    source: "yfinance daily closes (20d) · sentiment score",
    trading_impact: "Amplified ×1.20 during IDIOSYNCRATIC regime.",
    related: ["SENTIMENT", "IDIOSYNCRATIC", "MEAN_REVERT"],
  },

  // ── Archetype names ───────────────────────────────────────────────────────

  DIRECTIONAL_STRONG: {
    term: "DIRECTIONAL_STRONG",
    display: "DIRECTIONAL_STRONG",
    short: "High-conviction directional archetype: wide TP, tight SL, full Kelly. For high-confidence BULLISH signals.",
    long: `**DIRECTIONAL_STRONG** is a signal archetype (strategy profile) applied when confidence > 0.90.

It uses a wide take-profit target (+20–25% from entry) and a tight stop-loss (−5–8%), with full Kelly sizing. Designed for scenarios where multiple models agree strongly.`,
    trading_impact: "Full Kelly sizing. TP: +20–25% from limit. SL: −5–8%.",
    related: ["DIRECTIONAL_STD", "FRACTIONAL_KELLY", "BRACKET_ORDER"],
  },

  DIRECTIONAL_STD: {
    term: "DIRECTIONAL_STD",
    display: "DIRECTIONAL_STD",
    short: "Standard directional archetype: moderate TP/SL ratio. Used for confidence 0.80–0.90.",
    long: `**DIRECTIONAL_STD** is the standard directional archetype for conviction signals in the 0.80–0.90 confidence range.

It uses moderate take-profit and stop-loss targets, with fractional Kelly sizing.`,
    trading_impact: "Fractional Kelly (75%). TP: +12–18% from limit. SL: −8–12%.",
    related: ["DIRECTIONAL_STRONG", "FRACTIONAL_KELLY", "BRACKET_ORDER"],
  },

  MEAN_REVERT: {
    term: "MEAN_REVERT",
    display: "MEAN_REVERT",
    short: "Mean-reversion archetype: tight TP target (quick profit capture), wider SL tolerance.",
    long: `**MEAN_REVERT** is the archetype for CONTRARIAN signals expecting a near-term snap-back.

It uses a tight take-profit (capture the reversion quickly) and a wider stop-loss to avoid being stopped out by continued momentum before the reversion occurs.`,
    trading_impact: "Fractional Kelly (50%). TP: +5–10%. SL: −15–20%.",
    related: ["CONTRARIAN", "DIRECTIONAL_STD"],
  },

  SCALP_0DTE: {
    term: "SCALP_0DTE",
    display: "SCALP_0DTE",
    short: "Same-day expiry scalp archetype: small size, tight TP/SL for intraday momentum.",
    long: `**SCALP_0DTE** is the archetype for same-day (0DTE) options scalps.

It uses minimal Kelly fraction (25%), a tight take-profit, and a tight stop-loss to manage theta decay risk. Requires high intraday momentum confidence.`,
    trading_impact: "Kelly fraction 25%. TP: +30–50% from limit. SL: −20–25%.",
    related: ["VOL_PLAY", "THETA_BURN_SCORE"],
  },

  VOL_PLAY: {
    term: "VOL_PLAY",
    display: "VOL_PLAY",
    short: "Volatility-expansion archetype: targets IV mispricing when vol_ratio indicates options are cheap.",
    long: `**VOL_PLAY** is the archetype for the VOLATILITY model — it bets on implied volatility expanding toward realized vol.

Used when vol_ratio (realized/implied) > 1.3 and an earnings catalyst is 3–10 days away.`,
    trading_impact: "Sizing scales with vol_ratio. Kelly fraction up to 60%.",
    related: ["VOLATILITY", "FRACTIONAL_KELLY"],
  },

  // ── Sizing / risk terms ───────────────────────────────────────────────────

  NOTIONAL_ACCOUNT_SIZE: {
    term: "NOTIONAL_ACCOUNT_SIZE",
    display: "Notional Account Size",
    short: "Total capital base used for Kelly sizing — configurable via the UI. Does not need to match actual account balance.",
    long: `**Notional Account Size** is the synthetic capital figure used for Kelly criterion position sizing.

It can be set independently of the actual IBKR account balance, allowing conservative sizing while the account grows. Changing it rescales all new positions without affecting open orders.

Default: $25,000. Range: $5,000 – $250,000.`,
    source: "~/.tsla-alpha.env NOTIONAL_ACCOUNT_SIZE · configurable via /api/config/notional",
    trading_impact: "Kelly wager = notional × kelly_pct. Lower notional = smaller contracts.",
    related: ["FRACTIONAL_KELLY", "RISK_PCT"],
  },

  FRACTIONAL_KELLY: {
    term: "FRACTIONAL_KELLY",
    display: "Fractional Kelly",
    short: "Kelly criterion fraction used for sizing — base fraction determined by VIX tier, then multiplied by regime.",
    long: `**Fractional Kelly** is the core position-sizing formula.

The Kelly fraction is chosen based on the current VIX tier:
- VIX < 15 (LOW): 50% Kelly
- VIX 15–25 (NORMAL): 35% Kelly
- VIX > 25 (HIGH): 20% Kelly
- VIX > 35 (EXTREME): 10% Kelly

This base fraction is then multiplied by the regime_multiplier (0.5 if RISK_OFF, 1.0 otherwise) and capped at the risk_pct limit.`,
    formula: "final_kelly = vix_tier_fraction × regime_multiplier, capped at risk_pct",
    trading_impact: "Determines contracts sized: floor(notional × kelly × confidence / (price × 100 × 2))",
    related: ["REGIME_KELLY_MULTIPLIER", "VIX_MULTIPLIER", "NOTIONAL_ACCOUNT_SIZE", "RISK_PCT"],
  },

  REGIME_KELLY_MULTIPLIER: {
    term: "REGIME_KELLY_MULTIPLIER",
    display: "Regime Kelly Multiplier",
    short: "Multiplier applied to the Kelly fraction based on macro regime: 0.5 (RISK_OFF) or 1.0 (RISK_ON/NEUTRAL).",
    long: `**Regime Kelly Multiplier** scales the base Kelly fraction up or down based on the macro environment.

- **RISK_OFF** (VIX > 25 or SPY downtrend): multiplier = 0.5 — halves sizing to preserve capital
- **RISK_ON / NEUTRAL**: multiplier = 1.0 — full fraction applied

This is the primary capital-protection mechanism during market stress.`,
    formula: "regime_multiplier = 0.5 if RISK_OFF else 1.0",
    trading_impact: "RISK_OFF halves all new position sizes.",
    related: ["FRACTIONAL_KELLY", "RISK_OFF", "VIX_MULTIPLIER"],
  },

  VIX_MULTIPLIER: {
    term: "VIX_MULTIPLIER",
    display: "VIX Multiplier",
    short: "VIX-tier fractional Kelly: 50% (low VIX) → 10% (extreme VIX). Baseline before regime adjustment.",
    long: `**VIX Multiplier** is the VIX-tier Kelly fraction that sets the baseline before regime adjustment.

| VIX Level | Label   | Kelly Fraction |
|-----------|---------|----------------|
| < 15      | LOW     | 50%            |
| 15–25     | NORMAL  | 35%            |
| 25–35     | HIGH    | 20%            |
| > 35      | EXTREME | 10%            |`,
    formula: "VIX < 15 → 0.50; 15-25 → 0.35; 25-35 → 0.20; > 35 → 0.10",
    source: "^VIX via yfinance",
    trading_impact: "Base Kelly fraction before regime_multiplier is applied.",
    related: ["FRACTIONAL_KELLY", "REGIME_KELLY_MULTIPLIER"],
  },

  RISK_PCT: {
    term: "RISK_PCT",
    display: "Risk %",
    short: "Maximum capital at risk per trade as a percentage of notional account size. Hard cap on Kelly fraction.",
    long: `**Risk %** is the per-trade capital cap. Even if Kelly says size larger, no single trade can risk more than this fraction of the notional account.

Default: 2% of notional (per the archetype config). Configurable per archetype.`,
    formula: "max_risk = notional × risk_pct",
    trading_impact: "Caps position size: if Kelly sizing > risk_pct × notional, reduce to cap.",
    related: ["FRACTIONAL_KELLY", "NOTIONAL_ACCOUNT_SIZE"],
  },

  GROSS_PREMIUM_CAP: {
    term: "GROSS_PREMIUM_CAP",
    display: "Gross Premium Cap",
    short: "Maximum total premium outlay per trade in dollars — prevents oversized option purchases regardless of Kelly.",
    long: `**Gross Premium Cap** is an absolute dollar cap on the gross premium committed to a single trade.

It provides a dollar-denominated safety net when Kelly sizing, notional, or confidence are all high simultaneously.

Default: 5% of notional.`,
    formula: "max_premium = notional × gross_premium_cap_pct",
    trading_impact: "If premium × qty × 100 > cap, reduce qty to fit cap.",
    related: ["NOTIONAL_ACCOUNT_SIZE", "RISK_PCT", "FRACTIONAL_KELLY"],
  },

  // ── Order / execution terms ───────────────────────────────────────────────

  BRACKET_ORDER: {
    term: "BRACKET_ORDER",
    display: "Bracket Order",
    short: "A parent LIMIT order with two child OCO orders: Take-Profit LIMIT and Stop-Loss STP LMT.",
    long: `**Bracket Order** is the standard execution structure for all conviction trades.

It consists of:
1. **Parent order**: LIMIT BUY at the target limit price
2. **Take-Profit (TP)**: LIMIT SELL at the take-profit price (child, OCA group)
3. **Stop-Loss (SL)**: STP LMT SELL at the stop-loss price (child, OCA group)

The TP and SL are linked via an OCO (One Cancels Other) group — when one fills, the other is automatically cancelled.`,
    trading_impact: "Automatically manages exit risk. If bracket fails to place, the signal is marked FAILED — no fallback single leg.",
    related: ["OCO_GROUP", "TIF_DAY", "TAKE_PROFIT", "STOP_LOSS"],
  },

  OCO_GROUP: {
    term: "OCO_GROUP",
    display: "OCO Group",
    short: "One Cancels Other — two linked child orders where filling one auto-cancels the sibling.",
    long: `**OCO (One Cancels Other)** links the Take-Profit and Stop-Loss child orders in a bracket.

When either leg fills or is cancelled, IBKR automatically cancels the other. This prevents both TP and SL from filling on the same parent position.`,
    related: ["BRACKET_ORDER", "TAKE_PROFIT", "STOP_LOSS"],
  },

  TIF_OPG: {
    term: "TIF_OPG",
    display: "TIF OPG",
    short: "Time In Force: Opening. Order executes at market open or is cancelled — for pre-market bracket submissions.",
    long: `**TIF OPG (Opening)** specifies that the order must execute at the opening of the regular trading session or be cancelled.

Used for bracket orders submitted during pre-market so they enter at a known open price rather than at a potentially wide pre-market bid/ask.`,
    related: ["TIF_DAY", "BRACKET_ORDER"],
  },

  TIF_DAY: {
    term: "TIF_DAY",
    display: "TIF DAY",
    short: "Time In Force: Day. Order is live for the current trading session only — expires at close if not filled.",
    long: `**TIF DAY** means the order stays live for the current US equities session (9:30am–4:00pm ET) and is automatically cancelled at market close if not filled.

This is the standard TIF for intraday options orders.`,
    related: ["TIF_OPG", "BRACKET_ORDER"],
  },

  STP_LMT: {
    term: "STP_LMT",
    display: "STP LMT",
    short: "Stop-Limit order: triggers at the stop price, then executes as a LIMIT order at the limit price.",
    long: `**STP LMT (Stop-Limit)** is used for the Stop-Loss leg of a bracket order.

When the market price reaches the stop price, it converts to a LIMIT order at the limit price. This prevents catastrophic fills during a fast market — the order may not fill if the market gaps past the limit.`,
    related: ["BRACKET_ORDER", "STOP_LOSS", "UNDERLYING_STOP"],
  },

  UNDERLYING_STOP: {
    term: "UNDERLYING_STOP",
    display: "Underlying Stop",
    short: "Stop-loss triggered by the TSLA stock price (not the option price) — prevents gaps from eating the entire stop.",
    long: `**Underlying Stop** monitors the TSLA underlying price to trigger the option stop-loss.

Options can have wide bid/ask spreads, especially during fast moves. Using the underlying price as the stop trigger provides cleaner execution than waiting for the option to trade at the stop-loss price.`,
    related: ["STOP_LOSS", "STP_LMT", "BRACKET_ORDER"],
  },

  TAKE_PROFIT: {
    term: "TAKE_PROFIT",
    display: "Take Profit",
    short: "The target LIMIT price at which to close the position for the expected gain.",
    long: `**Take Profit** is the LIMIT SELL price for the upside exit leg of a bracket order.

It is determined by the archetype configuration and signal confidence. Higher confidence → wider take-profit target.`,
    related: ["STOP_LOSS", "BRACKET_ORDER", "OCO_GROUP"],
  },

  STOP_LOSS: {
    term: "STOP_LOSS",
    display: "Stop Loss",
    short: "The STP LMT price at which to close the position to limit downside risk.",
    long: `**Stop Loss** is the STP LMT SELL price for the downside exit leg of a bracket order.

It is set to limit the maximum premium lost on the position. Options can expire worthless, so the stop is placed well above zero to capture remaining time value.`,
    related: ["TAKE_PROFIT", "BRACKET_ORDER", "STP_LMT"],
  },

  EXPIRY_CLOSE: {
    term: "EXPIRY_CLOSE",
    display: "Expiry Close",
    short: "Automatic position close 15 minutes before expiration to avoid pin risk and assignment.",
    long: `**Expiry Close** is the Phase 9 expiry-exit mechanism.

All open options positions with same-day expiration are automatically sent a MARKET SELL order 15 minutes before close (3:45pm ET). This prevents:
- **Pin risk**: TSLA pinning at the strike at expiry
- **Assignment risk**: short calls/puts being exercised
- **Worthless expiry**: holding to zero when time value is recoverable`,
    related: ["BRACKET_ORDER", "TIF_DAY"],
  },

  // ── Composite-bias / scoring ──────────────────────────────────────────────

  COMPOSITE_BIAS: {
    term: "COMPOSITE_BIAS",
    display: "Composite Bias",
    short: "Weighted average of overnight index moves (Asia/Europe/US futures) into a directional score [−1, +1].",
    long: `**Composite Bias** is the pre-market panel's summary signal.

It is computed as the weighted average of index percent changes:
- Asia 30% (N225, HSI, SSE)
- Europe 40% (STOXX50E, DAX, FTSE)
- US Futures 30% (ES, NQ)

FX adjustments (DXY strength) can dampen the bullish contribution.`,
    formula: "composite = Σ(weight_i × change_i) / Σ(weight_i), clamped to [−1, +1]",
    source: "yfinance · overnight session data · refreshed pre-market",
    related: ["ASIA_WEIGHT", "EUROPE_WEIGHT", "US_FUTURES_WEIGHT", "FX_OVERRIDE", "PRE_MARKET_BIAS"],
  },

  ASIA_WEIGHT: {
    term: "ASIA_WEIGHT",
    display: "Asia Weight",
    short: "Asia contributes 30% to the composite pre-market bias (N225, HSI, SSE).",
    long: `**Asia Weight** = 30% of composite pre-market bias.\n\nIndex components: N225 (Nikkei 225), HSI (Hang Seng), SSE (Shanghai Composite).`,
    formula: "asia_contribution = 0.30 × avg(N225%, HSI%, SSE%)",
    related: ["COMPOSITE_BIAS", "EUROPE_WEIGHT", "US_FUTURES_WEIGHT"],
  },

  EUROPE_WEIGHT: {
    term: "EUROPE_WEIGHT",
    display: "Europe Weight",
    short: "Europe contributes 40% to the composite pre-market bias (STOXX50E, DAX, FTSE).",
    long: `**Europe Weight** = 40% of composite pre-market bias.\n\nIndex components: STOXX50E (Euro Stoxx 50), GDAXI (DAX), FTSE (FTSE 100).`,
    formula: "europe_contribution = 0.40 × avg(STOXX50E%, DAX%, FTSE%)",
    related: ["COMPOSITE_BIAS", "ASIA_WEIGHT", "US_FUTURES_WEIGHT"],
  },

  US_FUTURES_WEIGHT: {
    term: "US_FUTURES_WEIGHT",
    display: "US Futures Weight",
    short: "US futures contribute 30% to the composite pre-market bias (ES/S&P 500, NQ/Nasdaq).",
    long: `**US Futures Weight** = 30% of composite pre-market bias.\n\nComponents: ES (S&P 500 futures), NQ (Nasdaq 100 futures).`,
    formula: "us_contribution = 0.30 × avg(ES%, NQ%)",
    related: ["COMPOSITE_BIAS", "ASIA_WEIGHT", "EUROPE_WEIGHT"],
  },

  FX_OVERRIDE: {
    term: "FX_OVERRIDE",
    display: "FX Override",
    short: "DXY strength ≥ +0.5% dampens positive pre-market bias — strong USD is risk-off for risk assets.",
    long: `**FX Override** applies a negative adjustment to composite bias when the US Dollar Index (DXY) is rising strongly.

A rising DXY (≥ +0.5%) is historically bearish for risk assets including equities and TSLA. The override reduces the bullish composite bias to reflect this macro headwind.`,
    formula: "if DXY change ≥ +0.5%: composite_bias × 0.80",
    source: "DX-Y.NYB via yfinance",
    related: ["COMPOSITE_BIAS", "PRE_MARKET_BIAS"],
  },

  // ── Phase 14 prep (greeks + scoring) — values not yet shown ──────────────

  DELTA: {
    term: "DELTA",
    display: "Delta (δ)",
    short: "Rate of change of option price per $1 move in TSLA. +0.30 call = option gains $0.30 if TSLA rises $1.",
    long: `**Delta** (δ) measures how much the option price moves for each $1 change in the underlying stock.

- **Calls**: delta in (0, +1). Deep ITM → delta near 1. Deep OTM → delta near 0. ATM → ~0.50.
- **Puts**: delta in (−1, 0). Deep ITM put → delta near −1. Deep OTM → near 0.

**Phase 14 strike selection:**
Each archetype has a target delta range. DIRECTIONAL_STD targets |delta| ≈ 0.30 (±0.05 tolerance) for moderate leverage. SCALP_0DTE targets |delta| ≈ 0.50 for ATM gamma scalping. A strike outside the ±tolerance band is rejected.

Delta also approximates the probability the option expires in-the-money (rough heuristic).`,
    formula: "Call: Δ = N(d1). Put: Δ = N(d1) − 1. Where d1 = [ln(S/K) + (r + σ²/2)T] / (σ√T)",
    source: "IBKR modelGreeks (preferred) or Black-Scholes BS-compute from IV",
    trading_impact: "Target delta determines which strike is selected. Strike outside ±tolerance is rejected.",
    related: ["GAMMA", "VEGA", "THETA", "STRIKE_SELECTOR"],
  },

  GAMMA: {
    term: "GAMMA",
    display: "Gamma (Γ)",
    short: "Rate of change of delta per $1 move in TSLA — highest near the money and close to expiration.",
    long: `**Gamma (Γ)** measures how fast delta changes as TSLA moves.

High gamma (near-the-money, near expiry) means delta — and therefore P&L — can shift rapidly. 0DTE positions have very high gamma risk.

Gamma is always positive for long calls and puts. In Phase 14 strike selection, gamma is tracked for audit but not used as a filter criterion.`,
    formula: "Γ = N'(d1) / (S × σ × √T)",
    source: "IBKR modelGreeks or BS-compute",
    trading_impact: "Not a filter. Shown in the Strike Selection drill-down for trader context.",
    related: ["DELTA", "THETA", "SCALP_0DTE"],
  },

  THETA: {
    term: "THETA",
    display: "Theta (Θ)",
    short: "Time decay — dollars lost per calendar day as the option approaches expiration.",
    long: `**Theta (Θ)** is the daily time-value erosion of an option.

Long options lose Θ per day (negative theta). The closer to expiration, the faster the decay. 0DTE positions have extreme theta burn in the final hours.

**Phase 14 theta cap:**
Each archetype has a \`max_theta_pct_premium\` limit. A strike is rejected if |theta| / premium > cap. Example: DIRECTIONAL_STD cap = 5% — if an option costs $2 and theta is −$0.12/day (6%), it's rejected. SCALP_0DTE has a 25% cap (0DTE is theta-intensive by design).`,
    formula: "Θ_call = −(S·N'(d1)·σ)/(2√T) − r·K·e^(−rT)·N(d2), divided by 365 for daily",
    source: "IBKR modelGreeks or BS-compute",
    trading_impact: "Theta burn cap prevents selecting contracts where time decay eats profits before the trade works.",
    related: ["DELTA", "GAMMA", "STRIKE_SELECTOR", "SCALP_0DTE"],
  },

  VEGA: {
    term: "VEGA",
    display: "Vega (ν)",
    short: "Change in option price per 1-unit change in implied volatility. Long vega = benefits from rising IV.",
    long: `**Vega (ν)** measures how much the option price changes when implied volatility (IV) changes.

Long options have positive vega — rising IV increases option value. Vega is highest for ATM options with longer time to expiry.

**VOL_PLAY archetype:**
VOL_PLAY requires vega ≥ 0.10 per contract (Phase 14 floor). The strategy needs meaningful vega exposure to benefit from volatility expansion/compression.`,
    formula: "ν = S·√T·N'(d1). Value is per 1.0 change in σ (annualized fraction, not percent).",
    source: "IBKR modelGreeks or BS-compute",
    trading_impact: "VOL_PLAY filter: strike rejected if vega < 0.10. Other archetypes: vega shown for drill-down only.",
    related: ["DELTA", "THETA", "VOL_PLAY", "STRIKE_SELECTOR"],
  },

  IV: {
    term: "IV",
    display: "Implied Volatility",
    short: "Market's consensus forecast of TSLA price swings over the option's life — extracted from option prices.",
    long: `**Implied Volatility (IV)** is the volatility parameter implied by current option prices, not historical price moves.

High IV = expensive options (market expects big moves). Low IV = cheap options (calm expectations). The VOLATILITY model compares IV to 20-day realized volatility.`,
    source: "yfinance options chain · per-strike IV",
    related: ["VOLATILITY", "VOL_PLAY", "VEGA"],
  },

  DELTA_FIT: {
    term: "DELTA_FIT",
    display: "Delta Fit",
    short: "How well the signal's target delta matches the selected strike — quality score for strike selection.",
    long: `**Delta Fit** will score how closely the recommended strike matches the ideal delta for the archetype.

Phase 14 prep: this slot is reserved but not yet populated with live data.`,
    phase_note: "Added in Phase 14 — values not yet shown in the UI.",
    related: ["DELTA", "SPREAD_TIGHTNESS"],
  },

  SPREAD_TIGHTNESS: {
    term: "SPREAD_TIGHTNESS",
    display: "Spread Tightness",
    short: "Bid/ask spread as a percentage of mid — lower is better for entry/exit slippage.",
    long: `**Spread Tightness** measures liquidity quality at the target strike.

Formula: (ask − bid) / mid × 100%. Tighter spreads mean less slippage on entry and exit. Strikes with spread > 15% are deprioritized.`,
    formula: "(ask − bid) / mid × 100%",
    phase_note: "Added in Phase 14 — values not yet shown in the UI.",
    related: ["DELTA_FIT", "LIQUIDITY_SCORE"],
  },

  THETA_BURN_SCORE: {
    term: "THETA_BURN_SCORE",
    display: "Theta Burn Score",
    short: "Normalized daily theta erosion relative to premium — higher means faster decay risk.",
    long: `**Theta Burn Score** quantifies how much of the option premium is lost per day to time decay.

Formula: |theta| / mid_price × 100. A score > 5% means more than 5% of premium is lost daily — high for short-dated options.`,
    formula: "|theta| / mid_price × 100%",
    phase_note: "Added in Phase 14 — values not yet shown in the UI.",
    related: ["THETA", "SCALP_0DTE"],
  },

  LIQUIDITY_SCORE: {
    term: "LIQUIDITY_SCORE",
    display: "Liquidity Score",
    short: "Composite of OI, volume, and spread tightness — used to filter illiquid strikes.",
    long: `**Liquidity Score** is a composite measure used during strike selection to avoid illiquid options.

Components:
- Open Interest ≥ 100 (minimum threshold)
- Bid/ask spread tightness
- Daily volume relative to OI

Only strikes passing the minimum OI filter enter the signal pipeline.`,
    formula: "score = f(OI, volume, spread%)",
    phase_note: "Added in Phase 14 — values not yet shown in the UI.",
    related: ["SPREAD_TIGHTNESS", "DELTA_FIT"],
  },

  // ── Phase 13.5: Data-source repair terms ─────────────────────────────────

  DXY_SOURCE: {
    term: "DXY_SOURCE",
    display: "DXY (US Dollar Index)",
    short: "US Dollar Index — measures dollar strength vs 6 major currencies. Rising DXY = risk-off signal for equities.",
    long: `**DXY (US Dollar Index)** tracks the value of the US dollar against a basket of 6 major currencies (EUR 57.6%, JPY 13.6%, GBP 11.9%, CAD 9.1%, SEK 4.2%, CHF 3.6%).

**Source chain (Phase 13.5):**
- **Primary**: DX-Y.NYB — ICE US Dollar Index futures continuous contract, intraday available via yfinance.
- **Proxy fallback**: UUP — Invesco DB US Dollar Index Bullish ETF. ~1:1 directional correlation with DXY.
- **Unavailable**: If both fail, DXY is omitted from the composite confidence adjustment.

The legacy \`^DXY\` ticker was delisted from yfinance data feeds as of early 2026.`,
    source: "DX-Y.NYB (ICE futures) via yfinance — primary; UUP ETF as proxy fallback",
    trading_impact: "DXY move >0.5% adds ±0.20 to pre-market composite confidence. Rising DXY dampens bullish equity bias.",
    related: ["UUP_PROXY"],
    phase_note: "Updated in Phase 13.5 — ^DXY delisted; using DX-Y.NYB primary + UUP proxy.",
  },

  UUP_PROXY: {
    term: "UUP_PROXY",
    display: "UUP (DXY Proxy)",
    short: "Invesco DB US Dollar Index Bullish ETF — used as a DXY proxy when the primary DX-Y.NYB source fails.",
    long: `**UUP** (Invesco DB US Dollar Index Bullish Fund) is an ETF that tracks the Deutsche Bank Long US Dollar Futures index, which closely follows DXY composition.

Used as a **proxy** for DXY when the primary DX-Y.NYB futures contract is unavailable. The directional correlation is ~1:1, though the absolute price level is different (~28 vs DXY ~104).

When UUP is shown as the source, the system is using UUP % change as a stand-in for DXY % change. The confidence adjustment logic (>0.5% move = ±0.20 confidence) applies identically.

**Tradeoff**: UUP has slightly less granularity than the ICE futures contract and may lag by a few minutes during pre-market hours.`,
    source: "UUP ETF via yfinance — fallback when DX-Y.NYB returns empty data",
    trading_impact: "Same as DXY_SOURCE: >0.5% move adjusts pre-market confidence ±0.20.",
    related: ["DXY_SOURCE"],
    phase_note: "Added in Phase 13.5 as DXY fallback.",
  },

  SOURCE_DEGRADED: {
    term: "SOURCE_DEGRADED",
    display: "Feed Degraded",
    short: "A data source failed 3+ times consecutively and is in circuit-breaker mode — retrying after a 10-minute cooldown.",
    long: `**Feed Degraded** means a data source has exceeded the retry threshold and the circuit breaker has opened.

**Circuit breaker behavior (Phase 13.5):**
- 3 attempts with exponential backoff (1s, 4s, 16s)
- After 3 consecutive failures, the source is marked degraded for 10 minutes
- During cooldown, no requests are sent (prevents hammering a down endpoint)
- After cooldown, one probe is allowed; success resets the counter

**While degraded:**
- Data from other sources is still shown
- The UI displays "retrying at HH:MM"
- Signals continue from non-degraded sources

**Senate eFTS degradation** is typically caused by DNS failures on efts.senate.gov.`,
    trading_impact: "Signal generation continues from non-degraded sources. Congress signal shows as NEUTRAL if all sources degraded.",
    phase_note: "Added in Phase 13.5 — circuit breaker for Senate eFTS.",
  },

  PRICE_CONDITION: {
    term: "PRICE_CONDITION",
    display: "PriceCondition",
    short: "IBKR conditional stop: fires the stop-loss when the underlying stock price crosses a threshold, not the option premium.",
    long: `**PriceCondition** is an IBKR order condition that fires a child order when the underlying stock reaches a specified price.

**Why used for options brackets:**
Option premium fluctuates with implied volatility even when the underlying hasn't moved. A premium-based stop can fire prematurely during IV spikes. A PriceCondition on the underlying stock avoids this "volatility whipsaw."

**Phase 13.5 fix**: \`reqContractDetails()\` now used to verify \`conId > 0\` before building PriceCondition. Prior code used \`qualifyContracts()\`, which could return before metadata arrived, leaving \`conId=0\` and causing IBKR Error 321.

**Downgrade path**: If qualification fails, SL leg falls back to option-premium stop and a \`[BRACKET-DOWNGRADE]\` log line is emitted.`,
    source: "ib_insync PriceCondition · IBKR EWrapper",
    trading_impact: "Prevents SL leg from firing on IV noise. Requires conId > 0 from reqContractDetails().",
    related: ["IBKR_ERROR_321"],
    phase_note: "Phase 9 introduced PriceCondition; Phase 13.5 fixed conId=0 bug.",
  },

  IBKR_ERROR_321: {
    term: "IBKR_ERROR_321",
    display: "IBKR Error 321",
    short: "IBKR validation error: 'Invalid contract id' — caused by passing conId=0 to a PriceCondition before full qualification.",
    long: `**IBKR Error 321** ("Error validating request — Invalid contract id") is thrown when an order references a contract with \`conId=0\`.

**Root cause (Phase 9):**
\`qualifyContracts()\` could return before \`conId\` was populated, leaving it at 0. IBKR rejected the bracket order.

**Phase 13.5 fix:**
Replaced with \`reqContractDetails()\`, which returns explicit contract details. We verify \`conId > 0\` before proceeding. If not, the condition is skipped with a \`[BRACKET-DOWNGRADE]\` audit log.

**Error classification: FATAL** — not retried. This is a configuration error, not a connectivity blip.`,
    source: "IBKR EWrapper error code 321 · ib_insync reqContractDetails()",
    trading_impact: "Post-fix: PriceCondition has valid conId or SL downgrades gracefully to option-premium stop.",
    related: ["PRICE_CONDITION"],
    phase_note: "Added in Phase 13.5.",
  },

  // ── Phase 13.6 — System health / heartbeat terms ────────────────────────

  HEARTBEAT: {
    term: "HEARTBEAT",
    display: "Heartbeat",
    short: "A periodic liveness pulse emitted by each platform component — absence of a heartbeat means the process is dead or stuck.",
    long: `**Heartbeat** is a lightweight row written to SQLite (and published to NATS \`system.heartbeat\`) after every operational cycle of a component.

**Why heartbeats, not flags?**
A startup flag set to \`connected = true\` stays true even if the process crashes. A heartbeat is fresh only if the process is actually executing. If the publisher dies (bad service unit path, OOM, permissions), heartbeats stop — the indicator goes amber within one cadence window, then red.

**The 2026-04-13 incident:** publisher.service pointed at an abandoned path. Process exited 203/EXEC on every restart. Integrity dashboard showed engine/NATS/IBKR all green — because none measured publisher liveness. No signals generated all day. Phase 13.6 makes that failure impossible to miss.`,
    formula: "ok: age ≤ expected_max; degraded: expected_max < age ≤ 3×; error: age > 3× or never seen",
    source: "SQLite ~/tsla_alpha.db · NATS system.heartbeat",
    trading_impact: "Publisher heartbeat RED = no signals. Other components affect data quality.",
    related: ["PUBLISHER", "EXPECTED_CADENCE", "FLAPPING"],
    phase_note: "Added in Phase 13.6.",
  },

  EXPECTED_CADENCE: {
    term: "EXPECTED_CADENCE",
    display: "Expected Cadence",
    short: "The maximum healthy interval between heartbeats for a component — e.g. publisher: 30s, congress_trades: 3600s.",
    long: `**Expected Cadence** is the maximum interval between heartbeats for each component when running normally.

| Component | Max age (ok) |
|---|---|
| publisher | 30s |
| intel_refresh | 300s |
| options_chain_api | 120s |
| premarket | 120s (4:00–9:30 ET only) |
| congress_trades | 3600s |
| correlation_regime | 3600s |
| macro_regime | 300s |
| engine_subscriber | 90s |
| engine_ibkr_status | 180s |

**Thresholds:** age ≤ max → ok; max < age ≤ 3× → degraded; age > 3× → error.

**premarket off-hours exception:** outside 04:00–09:30 ET, premarket always shows ok with detail "skipped:off-hours".`,
    formula: "ok: age ≤ max; degraded: max < age ≤ 3×max; error: age > 3×max",
    trading_impact: "Stale intel_refresh or options_chain_api degrades signal quality. Publisher error = no new signals.",
    related: ["HEARTBEAT", "FLAPPING"],
    phase_note: "Added in Phase 13.6.",
  },

  FLAPPING: {
    term: "FLAPPING",
    display: "Flapping",
    short: "A component that rapidly oscillates between ok and error/degraded — indicates an unstable process, not a clean outage.",
    long: `**Flapping** occurs when a component repeatedly transitions between ok and error/degraded in a short window.

**How to detect:** Open the drill-down popover for any component and look at the sparkline (last 10 heartbeats). A healthy component shows consistent green dots. A flapping component shows alternating colors.

**Common causes:**
- Rate limiting: the source API throttles, the component backs off, retries, gets throttled again.
- OOM cycle: process killed and auto-restarted repeatedly.
- Network instability: intermittent IBKR or NATS connectivity.
- Cache TTL shorter than fetch latency: constant refetch failures.

**vs. clean outage:** Clean outage = long run of red dots after a gap. Flapping = alternating pattern.`,
    trading_impact: "Flapping components produce unreliable signals — confidence scores may reflect stale data despite occasional ok heartbeats.",
    related: ["HEARTBEAT", "EXPECTED_CADENCE"],
    phase_note: "Added in Phase 13.6.",
  },

  PUBLISHER: {
    term: "PUBLISHER",
    display: "Publisher",
    short: "The Python process (publisher.py) that runs signal-generation models and broadcasts TSLA options signals to the Go execution engine via NATS.",
    long: `**Publisher** is the Alpha Engine's primary signal-generation process (\`alpha_engine/publisher.py\`).

**Responsibilities:** runs SENTIMENT, OPTIONS_FLOW, MACRO, VOLATILITY, CONTRARIAN, EV_SECTOR, PREMARKET models; applies data-quality gates; sizes positions via Kelly; publishes to NATS \`tsla.alpha.signals\`; emits heartbeat after every cycle.

**Service unit:** \`publisher.service\`

**The 2026-04-13 incident:** Unit file pointed at an abandoned path (\`/home/builder/src/gemini/...\`). Process exited 203/EXEC on every restart. No signals generated all day.`,
    source: "alpha_engine/publisher.py · publisher.service",
    trading_impact: "Publisher dead = zero signals = no new positions opened.",
    related: ["HEARTBEAT", "EXPECTED_CADENCE"],
    phase_note: "Added in Phase 13.6.",
  },

  INTEL_REFRESH: {
    term: "INTEL_REFRESH",
    display: "Intel Refresh",
    short: "The intelligence aggregator (intel.py) that fetches news, VIX, SPY, options flow, macro, premarket, congress, and correlation data every 5 minutes.",
    long: `**Intel Refresh** aggregates all external data sources into the \`intel\` dict consumed by signal models.

**Sources:** news sentiment, VIX, SPY trend, earnings calendar, options flow (P/C ratio), catalyst (Musk + analysts), institutional flow, EV sector, macro regime, premarket, congress trades, correlation regime.

**Cache TTL:** 5 minutes. Heartbeat emitted after each full fetch (not cache hits).`,
    source: "alpha_engine/ingestion/intel.py",
    trading_impact: "Stale intel = models operate on old data. Expected cadence: 300s.",
    related: ["HEARTBEAT", "EXPECTED_CADENCE", "MACRO_REGIME"],
    phase_note: "Added in Phase 13.6.",
  },

  OPTIONS_CHAIN_API: {
    term: "OPTIONS_CHAIN_API",
    display: "Options Chain API",
    short: "The options chain cache that fetches TSLA strike/IV/OI data from yfinance or IBKR every 60s.",
    long: `**Options Chain API** fetches and caches the TSLA options chain.

**Source priority:** Off-hours + IBKR → IBKR snapshot; in-hours or IBKR unavailable → yfinance.

**Cache TTL:** 60s. Heartbeat emitted on each fresh fetch.

An empty or stale chain means the publisher cannot price strikes and will suppress signals.`,
    source: "alpha_engine/ingestion/options_chain.py",
    trading_impact: "Empty or stale chain = no priced strikes = no signals.",
    related: ["HEARTBEAT", "EXPECTED_CADENCE"],
    phase_note: "Added in Phase 13.6.",
  },

  ENGINE_SUBSCRIBER: {
    term: "ENGINE_SUBSCRIBER",
    display: "Engine Subscriber",
    short: "The Go execution engine's NATS subscriber goroutine — listens on tsla.alpha.signals and routes signals to IBKR or simulation.",
    long: `**Engine Subscriber** is the Go execution engine's primary signal-consumption loop (executor.service).

Subscribes to \`tsla.alpha.signals\`; applies confidence gate (> 0.8), dedup, rank cap, gross-outstanding cap; routes to IBKR subprocess or PaperPortfolio.

**Heartbeat:** 30s ticker goroutine writes to SQLite to prove the engine process is alive.`,
    source: "execution_engine/subscriber.go · executor.service",
    trading_impact: "Engine subscriber down = signals received but never executed.",
    related: ["HEARTBEAT", "IBKR_GATEWAY"],
    phase_note: "Added in Phase 13.6.",
  },

  IBKR_GATEWAY: {
    term: "IBKR_GATEWAY",
    display: "IBKR Gateway",
    short: "IBKR API Gateway liveness — measured by whether open_orders() roundtrips succeed within the engine's 60s poll cycle.",
    long: `**IBKR Gateway** tracks liveness of the connection to IBKR API Gateway (TWS or IB Gateway).

A 60s ticker goroutine calls \`OpenIBKROrders()\` (shells \`ingestion/ibkr_order.py open_orders\`). Success → ok; failure → degraded + error detail stored.

**Error codes to watch:** 1100 (connection lost), 1102 (connection restored after data loss).`,
    source: "execution_engine/ibkr_client.go · ingestion/ibkr_order.py",
    trading_impact: "IBKR gateway degraded = orders queue but cannot be submitted to broker.",
    related: ["HEARTBEAT", "ENGINE_SUBSCRIBER"],
    phase_note: "Added in Phase 13.6.",
  },

  NATS: {
    term: "NATS",
    display: "NATS",
    short: "NATS messaging server (127.0.0.1:4222) — the pub/sub bus between the Python Alpha Engine and Go execution engine.",
    long: `**NATS** is the lightweight pub/sub message broker connecting publisher to execution engine.

**Subjects:** \`tsla.alpha.signals\` (signals), \`tsla.alpha.sim\` (mode toggle), \`system.heartbeat\` (liveness pulses, Phase 13.6).

NATS runs 24/7 as a local process. If NATS goes down, the publisher retries connection on every cycle.`,
    source: "nats-server (local) · github.com/nats-io/nats.go",
    trading_impact: "NATS down = signals never reach execution engine = no order placement.",
    related: ["PUBLISHER", "ENGINE_SUBSCRIBER", "HEARTBEAT"],
    phase_note: "Added in Phase 13.6.",
  },

  // ── Phase 14: Greeks + Chop + Liquidity ──────────────────────────────────

  CHOP_REGIME: {
    term: "CHOP_REGIME",
    display: "Chop Regime",
    short: "Measures micro-structure conviction: TRENDING / MIXED / CHOPPY based on ADX, range ratio, BB squeeze, and RV/IV ratio.",
    long: `**Chop Regime** detects whether the TSLA tape has directional conviction or is churning sideways.

This is ORTHOGONAL to the macro regime (RISK_ON/OFF). Macro regime = systemic context; Chop regime = TSLA microstructure.

**Four components (0.25 weight each):**
1. **Range ratio** — 5-day avg (high−low) / |close−open|. High = lots of intraday range, little net move.
2. **ADX** — 14-period Wilder ADX on daily TSLA. ADX < 20 = no trend.
3. **Bollinger squeeze** — 20-day BB width / 90-day BB median. < 0.6 = compression (pre-chop signal).
4. **RV/IV ratio** — 5-day realized vol / ATM 30d IV. < 0.7 = price not moving like options expect.

**Composite score:**
- Score ≥ 0.75 → **CHOPPY**: block long-premium signals (DIRECTIONAL, MEAN_REVERT, SCALP_0DTE)
- Score [0.5, 0.75) → **MIXED**: down-weight long-premium ×0.7; VOL_PLAY ×1.1
- Score < 0.5 → **TRENDING**: no adjustment`,
    formula: "score = 0.25×(range>3) + 0.25×(ADX<20) + 0.25×(BB<0.6) + 0.25×(rv/iv<0.7)",
    source: "yfinance daily OHLCV + ATM IV from options chain · 5min refresh (market hours), 1h off-hours",
    trading_impact: "CHOPPY blocks DIRECTIONAL/SCALP emissions. MIXED applies ×0.7 confidence. VOL_PLAY benefits from MIXED/CHOPPY when IV is not too rich.",
    related: ["CORRELATION_REGIME", "MACRO_REGIME_KELLY", "VOL_PLAY"],
    phase_note: "Added in Phase 14.",
  },

  ADX: {
    term: "ADX",
    display: "ADX",
    short: "Average Directional Index (Wilder, 14-period) — measures trend strength; < 20 = no trend, > 25 = trending.",
    long: `**ADX (Average Directional Index)** is Wilder's measure of trend strength, not direction.

Computed from the ratio of the smoothed +DM / −DM indicators to the ATR over 14 daily periods. Values:
- ADX < 20: weak/no trend → potential chop
- ADX 20-25: emerging trend
- ADX > 25: established trend
- ADX > 40: strong trend

The chop regime uses ADX < 20 as one of four chop signals (0.25 weight).`,
    formula: "DX = 100 × |+DI − −DI| / (+DI + −DI); ADX = Wilder-smooth(DX, 14)",
    source: "yfinance daily OHLCV (TSLA)",
    trading_impact: "Low ADX contributes to CHOPPY chop score → blocks long-premium signals.",
    related: ["CHOP_REGIME"],
    phase_note: "Added in Phase 14.",
  },

  STRIKE_SELECTOR: {
    term: "STRIKE_SELECTOR",
    display: "Strike Selector",
    short: "Phase 14 greeks-aware strike selection pipeline: filters by TTM, liquidity, greeks availability, delta band, theta cap, then scores.",
    long: `**Strike Selector** (Phase 14) replaces moneyness-only selection with a 7-step Greeks-driven pipeline:

1. **TTM filter** — keep strikes within archetype's preferred days-to-expiry range.
2. **Liquidity gate** — reject OI < floor, volume < floor, spread > floor, bid < floor.
3. **Greeks availability** — skip rows where greeks could not be computed.
4. **Delta band** — keep |delta − target| ≤ tolerance.
5. **Theta cap** — reject if |theta| / premium > max_theta_pct_premium.
6. **Vega floor** — VOL_PLAY only: reject if vega < min_vega.
7. **Score** — weight: 50% delta-fit, 20% liquidity, 20% spread tightness, 10% theta efficiency.

If NO strike survives all filters, the signal is dropped with [STRIKE-REJECT] logged.`,
    formula: "score = 0.50×delta_fit + 0.20×liquidity + 0.20×spread + 0.10×theta",
    source: "options_chain.py (yfinance / IBKR) + pricing/greeks.py (BS compute)",
    trading_impact: "No relaxing of filters. Drop = no emission. Prevents stale/illiquid trades.",
    related: ["DELTA", "THETA", "VEGA", "LIQUIDITY_HEADROOM"],
    phase_note: "Added in Phase 14.",
  },

  LIQUIDITY_HEADROOM: {
    term: "LIQUIDITY_HEADROOM",
    display: "Liquidity Headroom",
    short: "How far above the liquidity floors the selected strike sits. E.g., volume 320/50 = 6.4× headroom.",
    long: `**Liquidity Headroom** shows the margin above each liquidity floor for the selected strike:

- **Volume headroom**: today's volume / MIN_OPTION_VOLUME_TODAY floor (default 50)
- **OI headroom**: open interest / MIN_OPTION_OPEN_INTEREST floor (default 500)
- **Spread headroom**: floor / actual spread (higher = tighter)
- **Bid headroom**: actual bid / MIN_ABSOLUTE_BID floor (default $0.10)

A headroom of 1.0× means barely passing. 5×+ means comfortable buffer.

Color coding in the pending order "Liq" chip:
- Green: > 2× headroom on all floors
- Amber: 1–2× on at least one
- Red: < 1× (below floor — engine will re-verify and reject)`,
    formula: "headroom_x = actual_value / floor_value for each gate",
    source: "Phase 14 strike_selector.py · env vars: MIN_OPTION_OPEN_INTEREST, MIN_OPTION_VOLUME_TODAY, MAX_BID_ASK_PCT, MIN_ABSOLUTE_BID",
    trading_impact: "Low headroom signals that a strike is marginally liquid — may degrade between emit and execution. Engine re-checks at placement time.",
    related: ["STRIKE_SELECTOR", "PENNY_CONTRACT", "MIN_OPTION_VOLUME_TODAY"],
    phase_note: "Added in Phase 14.",
  },

  MIN_OPTION_VOLUME_TODAY: {
    term: "MIN_OPTION_VOLUME_TODAY",
    display: "Min Volume Today",
    short: "Require at least N contracts traded today on this strike. Default: 50. Prevents trading dead/stale contracts.",
    long: `**MIN_OPTION_VOLUME_TODAY** is a liquidity gate that rejects any strike with fewer than N contracts traded today.

Root cause: on 2026-04-12, a $0.05 TSLA $365 CALL order sat unfilled for 22 hours because the strike had essentially no activity. Volume = 0 during the order lifetime.

Setting MIN_OPTION_VOLUME_TODAY=50 requires at least 50 contracts traded today before the strike is considered liquid enough to trade. This is a **runtime-configurable** env var — tune without redeploy via \`.tsla-alpha.env\`.`,
    formula: "row.volume >= MIN_OPTION_VOLUME_TODAY (default 50)",
    source: "yfinance daily volume column / IBKR ticker.volume · .tsla-alpha.env",
    trading_impact: "Rejects strikes with zero/thin volume. Prevents 22-hour stale-order scenarios.",
    related: ["MIN_OPTION_OPEN_INTEREST", "PENNY_CONTRACT", "LIQUIDITY_HEADROOM"],
    phase_note: "Added in Phase 14. Anti-stale-trade control.",
  },

  MIN_OPTION_OPEN_INTEREST: {
    term: "MIN_OPTION_OPEN_INTEREST",
    display: "Min Open Interest",
    short: "Require OI >= N on the selected strike. Default: 500. Low OI = wide spreads, hard fills, price impact.",
    long: `**MIN_OPTION_OPEN_INTEREST** rejects any strike with open interest below the floor.

Open interest (OI) is the count of outstanding contracts. High OI → multiple market participants → tighter bid/ask spreads → easier fills.

Default 500 is conservative for TSLA (liquid stock); adjust up for tighter discipline or down for smaller OTM wings.

Runtime-configurable via \`MIN_OPTION_OPEN_INTEREST\` env var.`,
    formula: "row.open_interest >= MIN_OPTION_OPEN_INTEREST (default 500)",
    source: "yfinance / IBKR · .tsla-alpha.env",
    trading_impact: "Low OI = wide spread = unfavorable fills. Rejects such strikes before signal emission.",
    related: ["MIN_OPTION_VOLUME_TODAY", "LIQUIDITY_HEADROOM"],
    phase_note: "Added in Phase 14.",
  },

  MAX_BID_ASK_PCT: {
    term: "MAX_BID_ASK_PCT",
    display: "Max Bid-Ask Spread %",
    short: "Reject if (ask−bid)/mid > threshold. Default: 15%. Wide spreads mean you pay too much at entry/exit.",
    long: `**MAX_BID_ASK_PCT** rejects strikes where the percentage spread between bid and ask is too wide.

Formula: spread% = (ask − bid) / ((ask + bid) / 2). If spread% > MAX_BID_ASK_PCT (default 15%), the strike is rejected.

A 15% spread on a $2 option means the bid/ask gap is $0.30 — you lose $0.15 immediately on entry and another $0.15 on exit. That's a $30/contract round-trip friction on top of commissions.

Configurable via \`MAX_BID_ASK_PCT\` env var.`,
    formula: "(ask − bid) / mid ≤ MAX_BID_ASK_PCT (default 0.15)",
    source: "options chain bid/ask · .tsla-alpha.env",
    trading_impact: "Wide spreads destroy edge. Reject before emission, not after a bad fill.",
    related: ["MIN_ABSOLUTE_BID", "LIQUIDITY_HEADROOM"],
    phase_note: "Added in Phase 14.",
  },

  MIN_ABSOLUTE_BID: {
    term: "MIN_ABSOLUTE_BID",
    display: "Min Absolute Bid",
    short: "Reject any contract whose bid < $0.10. Kills penny contracts — zero real market depth.",
    long: `**MIN_ABSOLUTE_BID** (anti-penny filter) rejects any option contract where the bid is below the minimum dollar threshold.

**Penny contracts** (bid < $0.10) have essentially no real market depth:
- Wide percentage spreads (50%+ even on a $0.05/$0.10 market)
- Often untradeable in practice — fills require luck, not skill
- Commissions ($0.65/contract minimum) can exceed the entire option value

Default $0.10 is conservative. Adjust via \`MIN_ABSOLUTE_BID\` env var.`,
    formula: "row.bid >= MIN_ABSOLUTE_BID (default $0.10)",
    source: "options chain bid · .tsla-alpha.env",
    trading_impact: "Eliminates deep OTM, near-expiry contracts that look cheap but are untradeable. Prevents the commission-negative scenario.",
    related: ["MAX_BID_ASK_PCT", "LIQUIDITY_HEADROOM", "PENNY_CONTRACT"],
    phase_note: "Added in Phase 14.",
  },

  PENNY_CONTRACT: {
    term: "PENNY_CONTRACT",
    display: "Penny Contract",
    short: "An option with bid < $0.10. Zero meaningful market depth; fills require luck; commissions may exceed option value.",
    long: `A **penny contract** is any option with a bid price below $0.10. These are nearly always deep out-of-the-money options close to expiry.

**Why we avoid them:**
- Bid/ask spread is often the entire option value (e.g., bid $0.01 / ask $0.05 = 133% spread)
- IBKR minimum commission is $1/contract — on a $0.05 contract, commission = 20× option value
- Fills depend on luck, not market depth; often sit unfilled for hours
- Classic root cause of the 2026-04-12 stale TSLA CALL incident

Phase 14 MIN_ABSOLUTE_BID=$0.10 prevents emitting signals on penny contracts.`,
    formula: "bid < $0.10 → reject",
    source: "options chain bid price",
    trading_impact: "Never trade penny contracts. Phase 14 hard-blocks them at the publisher layer.",
    related: ["MIN_ABSOLUTE_BID", "MIN_OPTION_VOLUME_TODAY", "LIQUIDITY_HEADROOM"],
    phase_note: "Added in Phase 14.",
  },
};

/**
 * Look up a glossary entry by canonical key.
 * Returns undefined if the key is not in the glossary.
 */
export function lookupTerm(key: string): GlossaryEntry | undefined {
  return GLOSSARY[key];
}
