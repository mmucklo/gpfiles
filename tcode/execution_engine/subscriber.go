package main

import (
	"encoding/json"
	"fmt"
	"log"
	"sync"
	"time"

	"github.com/nats-io/nats.go"
)

// AlphaSignal represents the incoming conviction signal from the Python Alpha Engine.
type AlphaSignal struct {
	ModelID           string  `json:"model_id"`
	Direction         string  `json:"direction"`
	Confidence        float64 `json:"confidence"`
	Timestamp         float64 `json:"timestamp"`
	Ticker            string  `json:"ticker"`
	UnderlyingPrice   float64 `json:"underlying_price"`
	PriceSource       string  `json:"price_source"`
	StrategyCode      string  `json:"strategy_code"`
	RecommendedStrike float64 `json:"recommended_strike"`
	ShortStrike       float64 `json:"short_strike"`
	LongStrike        float64 `json:"long_strike"`
	IsSpread          bool    `json:"is_spread"`
	RecommendedExpiry string  `json:"recommended_expiry"`
	OptionType        string  `json:"option_type"`
	Action            string  `json:"action"`
	ExpirationDate    string  `json:"expiration_date"`
	TargetLimitPrice  float64 `json:"target_limit_price"`
	TakeProfitPrice   float64 `json:"take_profit_price"`
	StopLossPrice     float64 `json:"stop_loss_price"`
	KellyWagerPct     float64 `json:"kelly_wager_pct"`
	Quantity          int     `json:"quantity"`
	ConfidenceRationale string                 `json:"confidence_rationale"`
	ImpliedVolatility   float64                `json:"implied_volatility"`
	SpotSources         map[string]interface{} `json:"spot_sources,omitempty"`

	// Execution result — set after order placement, zero-value before.
	IBKROrderID int    `json:"ibkr_order_id,omitempty"` // > 0 when order reached broker
	ExecStatus  string `json:"exec_status,omitempty"`   // "submitted" | "failed" | "sim_filled" | "rejected"
	ExecError   string `json:"exec_error,omitempty"`    // non-empty on failure
}

// orderState tracks the last-known status for a signal fingerprint so we can
// suppress duplicate PlaceIBKROrder calls and log only on status transitions.
type orderState struct {
	OrderID int
	Status  string
}

// activeStatuses is the set of IBKR statuses that mean an order is already
// live at the broker.  A signal whose fingerprint matches one of these should
// NOT trigger a new order submission.
var activeStatuses = map[string]bool{
	"PreSubmitted":  true,
	"Submitted":     true,
	"PendingSubmit": true,
	"Filled":        true,
}

// SignalSubscriber listens for Alpha Engine broadcasts and triggers execution.
type SignalSubscriber struct {
	Conn       *nats.Conn
	Executor   *IBKRExecutor
	Pricing    *PricingEngine
	Guard      *LiveCapitalGuard
	Compliance *ComplianceGuard
	Archive    *ArchiveSink

	// orderFingerprints deduplicates signal → order calls.
	// Key: "<strike>_<expiry>_<action>_<qty>"  Value: last-known orderState.
	orderFingerprints   map[string]orderState
	orderFingerprintsMu sync.Mutex
}

func NewSignalSubscriber(natsURL string, executor *IBKRExecutor, pricing *PricingEngine, guard *LiveCapitalGuard, compliance *ComplianceGuard, archive *ArchiveSink) *SignalSubscriber {
	sub := &SignalSubscriber{
		Executor:          executor,
		Pricing:           pricing,
		Guard:             guard,
		Compliance:        compliance,
		Archive:           archive,
		orderFingerprints: make(map[string]orderState),
	}
	nc, err := nats.Connect(natsURL)
	if err != nil {
		log.Printf("NATS Connection Error: %v (Proceeding with offline mode for test)", err)
		return sub
	}
	sub.Conn = nc
	return sub
}

// signalFingerprint returns a dedup key for a conviction signal.
func signalFingerprint(ticker, optType, expiry, action string, strike float64, qty int) string {
	return fmt.Sprintf("%s_%s_%s_%s_%.2f_%d", ticker, optType, expiry, action, strike, qty)
}

func (s *SignalSubscriber) Start() {
	if s.Conn == nil {
		fmt.Println("Signal Subscriber: Offline Mode. No NATS server found.")
		return
	}

	fmt.Println("Signal Subscriber: Listening for 'tsla.alpha.signals'...")

	// Async subscription to ensure low-latency signal consumption.
	_, err := s.Conn.Subscribe("tsla.alpha.signals", func(m *nats.Msg) {
		fmt.Printf("DEBUG: Received raw message on 'tsla.alpha.signals': %s\n", string(m.Data))
		start := time.Now()
		var sig AlphaSignal
		if err := json.Unmarshal(m.Data, &sig); err != nil {
			log.Printf("Signal Decoding Error: %v", err)
			return
		}

		fmt.Printf("Consensus Signal Received: %s [%.2f] @ %.0f\n", sig.Direction, sig.Confidence, sig.Timestamp)

		// Record to Archive Sink (non-execution path — before gating)
		if s.Archive != nil {
			s.Archive.RecordSignal(sig.ModelID, sig.Direction, sig.Confidence)
		}

		// Update Prometheus Metrics
		SignalAgreement.Set(sig.Confidence)

		// Non-conviction signals (IDLE_SCAN, heartbeat) are recorded immediately without execution.
		if !(sig.Direction == "BULLISH" && sig.Confidence > 0.8) {
			AddSignal(sig)
			return
		}

		// ── Conviction signal: apply guards then execute ──────────────────────

		if s.Guard.KillSwitch {
			sig.ExecStatus = "rejected"
			sig.ExecError = "kill_switch_active"
			AddSignal(sig)
			fmt.Println("SIGNAL BLOCKED: Live Capital Guard Kill-Switch is active.")
			return
		}

		ticker := sig.Ticker
		if ticker == "" {
			ticker = "TSLA"
		}
		strike := sig.RecommendedStrike
		if sig.IsSpread {
			strike = sig.ShortStrike
		}

		if err := s.Compliance.CheckPDTRule(); err != nil {
			sig.ExecStatus = "rejected"
			sig.ExecError = err.Error()
			AddSignal(sig)
			fmt.Printf("SIGNAL BLOCKED: %v\n", err)
			return
		}

		signature := s.Executor.Portfolio.GetContractSignature(ticker, sig.OptionType, sig.ExpirationDate, strike)
		if err := s.Compliance.CheckWashSaleRule(signature); err != nil {
			sig.ExecStatus = "rejected"
			sig.ExecError = err.Error()
			AddSignal(sig)
			fmt.Printf("SIGNAL BLOCKED: %v\n", err)
			return
		}

		price := sig.TargetLimitPrice
		if price <= 0 {
			sig.ExecStatus = "rejected"
			sig.ExecError = "target_limit_price_zero"
			AddSignal(sig)
			fmt.Printf("SIGNAL REJECTED: TargetLimitPrice=0 for %s — publisher failed to price, skipping execution\n", ticker)
			return
		}

		qty := sig.Quantity
		if qty <= 0 {
			qty = 1
		}

		action := sig.Action
		if action == "" {
			action = "BUY"
		}

		// ── Route execution by mode ────────────────────────────────────────────
		switch ActiveExecutionMode {

		case ModeIBKRPaper, ModeIBKRLive:
			// Real broker path: shell out to ibkr_order.py.
			// No internal simulator fallback — if the subprocess fails, the
			// signal is marked FAILED and the trade does not happen.
			contract := OptionContract{
				Symbol:     ticker,
				OptionType: sig.OptionType,
				Strike:     strike,
				Expiry:     sig.ExpirationDate,
			}
			absQty := qty
			if absQty < 0 {
				absQty = -absQty
			}

			// ── Dedup: skip if an order with the same fingerprint is already active ──
			fp := signalFingerprint(ticker, sig.OptionType, sig.ExpirationDate, action, strike, absQty)
			s.orderFingerprintsMu.Lock()
			prev, exists := s.orderFingerprints[fp]
			s.orderFingerprintsMu.Unlock()
			if exists && activeStatuses[prev.Status] {
				log.Printf("[SKIP] duplicate signal — orderId=%d status=%s fingerprint=%s",
					prev.OrderID, prev.Status, fp)
				sig.IBKROrderID = prev.OrderID
				sig.ExecStatus = "submitted"
				AddSignal(sig)
				break
			}

			result, err := PlaceIBKROrder(contract, action, absQty, price)
			if err != nil {
				sig.ExecStatus = "failed"
				sig.ExecError = err.Error()
				AddSignal(sig)
				log.Printf("IBKR ORDER FAILED: %s %dx %s strike=%.2f expiry=%s price=%.4f — %v",
					action, absQty, ticker, strike, sig.ExpirationDate, price, err)
				return
			}

			// Record new fingerprint state.
			s.orderFingerprintsMu.Lock()
			s.orderFingerprints[fp] = orderState{OrderID: result.OrderID, Status: result.Status}
			s.orderFingerprintsMu.Unlock()

			sig.IBKROrderID = result.OrderID
			sig.ExecStatus = "submitted"
			AddSignal(sig)

			// Log only when this is genuinely a new order (not a status repeat).
			if !exists || prev.Status != result.Status {
				log.Printf("IBKR ORDER PLACED: orderId=%d status=%s symbol=%s strike=%.2f expiry=%s price=%.4f qty=%d",
					result.OrderID, result.Status, ticker, strike, sig.ExpirationDate, price, absQty)
			}

			// Record in trade log as a real broker order (not a simulated fill).
			cost := float64(absQty) * price * 100
			AddTradeLog(TradeLog{
				Time:     time.Now(),
				Ticker:   signature,
				Action:   action,
				Quantity: absQty,
				Price:    price,
				Cost:     cost,
			})

			if s.Archive != nil {
				s.Archive.RecordTrade(ticker, action, absQty, price, 0.0)
			}

		case ModeSimulation:
			// Internal simulation path: use deterministic FillModel.
			// PaperPortfolio tracks positions; no broker subprocess is invoked.
			buyQty := qty
			if action == "SELL" {
				buyQty = -qty
			}
			s.Executor.ExecuteOrder(ticker, sig.OptionType, strike, sig.ExpirationDate, buyQty, price)
			sig.ExecStatus = "sim_filled"
			AddSignal(sig)

			if s.Archive != nil {
				s.Archive.RecordTrade(ticker, action, qty, price, 0.0)
			}

		default:
			// Unknown or unset mode — REJECT. Never fall back silently.
			sig.ExecStatus = "rejected"
			sig.ExecError = fmt.Sprintf("unknown_execution_mode:%s", ActiveExecutionMode)
			AddSignal(sig)
			log.Printf("SIGNAL REJECTED: unknown or unset EXECUTION_MODE=%q — "+
				"set EXECUTION_MODE to IBKR_PAPER or SIMULATION", ActiveExecutionMode)
			return
		}

		// Record latency for all executed signals
		ExecutionLatency.Observe(time.Since(start).Seconds())
		TradeCount.Inc()
	})

	_, err = s.Conn.Subscribe("tsla.alpha.sim", func(m *nats.Msg) {
		UpdateSimState(m.Data)
	})

	if err != nil {
		log.Fatalf("Subscription Error: %v", err)
	}
}

func (s *SignalSubscriber) Close() {
	if s.Conn != nil {
		s.Conn.Close()
	}
}
