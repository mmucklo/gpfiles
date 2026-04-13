package main

import (
	"encoding/json"
	"fmt"
	"log"
	"math"
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
	TakeProfitPrice            float64 `json:"take_profit_price"`
	StopLossPrice              float64 `json:"stop_loss_price"`
	StopLossUnderlyingPrice    float64 `json:"stop_loss_underlying_price,omitempty"`
	KellyWagerPct              float64 `json:"kelly_wager_pct"`
	Quantity          int     `json:"quantity"`
	ConfidenceRationale string                 `json:"confidence_rationale"`
	ImpliedVolatility   float64                `json:"implied_volatility"`
	SpotSources         map[string]interface{} `json:"spot_sources,omitempty"`

	// Execution result — set after order placement, zero-value before.
	IBKROrderID int    `json:"ibkr_order_id,omitempty"` // > 0 when order reached broker
	ExecStatus  string `json:"exec_status,omitempty"`   // "submitted" | "failed" | "sim_filled" | "rejected"
	ExecError   string `json:"exec_error,omitempty"`    // non-empty on failure

	// Rank assigned at placement time (for pending-cap comparisons in UI).
	SignalRank float64 `json:"signal_rank,omitempty"`
}

// orderState tracks the last-known status for a signal fingerprint so we can
// suppress duplicate PlaceIBKROrder calls and log only on status transitions.
type orderState struct {
	OrderID int
	Status  string
}

// pendingOrderInfo stores rank and originating signal data alongside an IBKR
// pending order.  Kept in-memory; TODO(phase-9): persist to SQLite for restart durability.
type pendingOrderInfo struct {
	OrderID  int
	Rank     float64
	Signal   AlphaSignal
	PlacedAt time.Time
}

// capReplacementEvent is a ring-buffer entry for the UI event feed.
type capReplacementEvent struct {
	Ts            time.Time
	Kind          string // "REPLACE" or "REJECT-CAP"
	CancelledID   int
	CancelledRank float64
	IncomingRank  float64
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

// ── Bug 2 fix: package-level dedup map protected by sync.RWMutex ─────────────
// Lifted out of SignalSubscriber so the mutex is explicit and any future helper
// (e.g. the cap-check path) cannot race against the NATS handler goroutine.

var (
	orderDedupMu sync.RWMutex
	orderDedup   = map[string]orderState{}
)

// inFlightStatuses extends activeStatuses with an internal sentinel used while
// the order placement is in progress.  Any goroutine that sees "pending" should
// also skip re-entry, ensuring exactly one goroutine places per fingerprint.
var inFlightStatuses = map[string]bool{
	"PreSubmitted":  true,
	"Submitted":     true,
	"PendingSubmit": true,
	"Filled":        true,
	"pending":       true, // in-flight sentinel; cleared on error
}

// checkAndMarkOrder atomically checks whether a fingerprint has an active or
// in-flight order and, if not, reserves it with a "pending" sentinel.
// Returns (shouldPlace bool, prevState orderState).
// The entire check+mark is under the write lock so concurrent goroutines cannot
// both return shouldPlace=true for the same fingerprint (Bug 2 fix).
func checkAndMarkOrder(fp string, newState orderState) (shouldPlace bool, prev orderState) {
	orderDedupMu.Lock()
	defer orderDedupMu.Unlock()
	prev = orderDedup[fp]
	if prev.Status != "" && inFlightStatuses[prev.Status] {
		return false, prev
	}
	orderDedup[fp] = newState
	return true, prev
}

// updateOrderState records the latest known broker state for a fingerprint.
func updateOrderState(fp string, state orderState) {
	orderDedupMu.Lock()
	defer orderDedupMu.Unlock()
	orderDedup[fp] = state
}

// readOrderState returns the last-known state for a fingerprint (read-only).
func readOrderState(fp string) (orderState, bool) {
	orderDedupMu.RLock()
	defer orderDedupMu.RUnlock()
	s, ok := orderDedup[fp]
	return s, ok
}

// ── Pending-order cap ─────────────────────────────────────────────────────────

var (
	pendingCapMu     sync.RWMutex
	pendingCapOrders = map[int]pendingOrderInfo{}
)

var (
	capEventsMu sync.RWMutex
	capEvents   []capReplacementEvent // last 10 events
)

func recordCapEvent(ev capReplacementEvent) {
	capEventsMu.Lock()
	defer capEventsMu.Unlock()
	capEvents = append([]capReplacementEvent{ev}, capEvents...)
	if len(capEvents) > 10 {
		capEvents = capEvents[:10]
	}
}

// GetCapEvents returns the last ≤10 cap replacement events for the UI feed.
func GetCapEvents() []capReplacementEvent {
	capEventsMu.RLock()
	defer capEventsMu.RUnlock()
	out := make([]capReplacementEvent, len(capEvents))
	copy(out, capEvents)
	return out
}

// computeRank scores a signal on [0,1] using confidence, return-on-cost, and recency.
//
//	rank = confidence * 0.5
//	     + min((TakeProfit - LimitPrice) / LimitPrice, 1.0) * 0.3
//	     + exp(-age_seconds / 600) * 0.2
func computeRank(sig AlphaSignal) float64 {
	// Confidence component
	conf := math.Max(0, math.Min(1, sig.Confidence))

	// Return-on-cost: (TP - LimitPrice) / LimitPrice, capped at 1.0
	roi := 0.0
	if sig.TargetLimitPrice > 0 && sig.TakeProfitPrice > sig.TargetLimitPrice {
		roi = math.Min((sig.TakeProfitPrice-sig.TargetLimitPrice)/sig.TargetLimitPrice, 1.0)
	}

	// Recency: e^(-age/600) — fresh≈1, 10 min≈0.37, 30 min≈0.05
	ageSec := time.Since(time.Unix(int64(sig.Timestamp), 0)).Seconds()
	if ageSec < 0 {
		ageSec = 0
	}
	recency := math.Exp(-ageSec / 600.0)

	rank := conf*0.5 + roi*0.3 + recency*0.2
	return math.Max(0, math.Min(1, rank))
}

// lowestRankedPending returns the pending order with the lowest rank.
func lowestRankedPending() (lowest pendingOrderInfo, found bool) {
	pendingCapMu.RLock()
	defer pendingCapMu.RUnlock()
	for _, info := range pendingCapOrders {
		if !found || info.Rank < lowest.Rank {
			lowest = info
			found = true
		}
	}
	return
}

// addPendingOrder records a newly placed order in the cap tracker.
func addPendingOrder(orderID int, rank float64, sig AlphaSignal) {
	pendingCapMu.Lock()
	defer pendingCapMu.Unlock()
	pendingCapOrders[orderID] = pendingOrderInfo{
		OrderID:  orderID,
		Rank:     rank,
		Signal:   sig,
		PlacedAt: time.Now(),
	}
}

// removePendingOrder removes a cancelled or filled order from the cap tracker.
func removePendingOrder(orderID int) {
	pendingCapMu.Lock()
	defer pendingCapMu.Unlock()
	delete(pendingCapOrders, orderID)
}

// activePendingCount returns the number of orders tracked as pending.
func activePendingCount() int {
	pendingCapMu.RLock()
	defer pendingCapMu.RUnlock()
	return len(pendingCapOrders)
}

// GetPendingCapOrders returns a snapshot of tracked pending orders for the UI.
func GetPendingCapOrders() []pendingOrderInfo {
	pendingCapMu.RLock()
	defer pendingCapMu.RUnlock()
	out := make([]pendingOrderInfo, 0, len(pendingCapOrders))
	for _, v := range pendingCapOrders {
		out = append(out, v)
	}
	return out
}

// ── SignalSubscriber ──────────────────────────────────────────────────────────

// SignalSubscriber listens for Alpha Engine broadcasts and triggers execution.
type SignalSubscriber struct {
	Conn       *nats.Conn
	Executor   *IBKRExecutor
	Pricing    *PricingEngine
	Guard      *LiveCapitalGuard
	Compliance *ComplianceGuard
	Archive    *ArchiveSink
}

func NewSignalSubscriber(natsURL string, executor *IBKRExecutor, pricing *PricingEngine, guard *LiveCapitalGuard, compliance *ComplianceGuard, archive *ArchiveSink) *SignalSubscriber {
	sub := &SignalSubscriber{
		Executor:   executor,
		Pricing:    pricing,
		Guard:      guard,
		Compliance: compliance,
		Archive:    archive,
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
			// checkAndMarkOrder is atomic (RWMutex-protected) — Bug 2 fix.
			shouldPlace, prev := checkAndMarkOrder(fp, orderState{OrderID: 0, Status: "pending"})
			if !shouldPlace {
				log.Printf("[SKIP] duplicate signal — orderId=%d status=%s fingerprint=%s",
					prev.OrderID, prev.Status, fp)
				sig.IBKROrderID = prev.OrderID
				sig.ExecStatus = "submitted"
				AddSignal(sig)
				break
			}

			// ── Pending-order cap: rank-based replacement ──────────────────────
			incomingRank := computeRank(sig)
			maxPending := envIntOrDefault("MAX_PENDING_ORDERS", 2)

			if activePendingCount() >= maxPending {
				lowest, found := lowestRankedPending()
				if !found || incomingRank <= lowest.Rank {
					log.Printf("[REJECT-CAP] pending queue full (%d/%d), incoming rank=%.3f <= lowest=%.3f",
						activePendingCount(), maxPending, incomingRank, lowest.Rank)
					sig.ExecStatus = "rejected"
					sig.ExecError = fmt.Sprintf("pending_cap_full:rank=%.3f:cutoff=%.3f", incomingRank, lowest.Rank)
					// Clear the tentative dedup entry so future higher-rank signals aren't blocked.
					updateOrderState(fp, orderState{})
					AddSignal(sig)
					recordCapEvent(capReplacementEvent{
						Ts:            time.Now(),
						Kind:          "REJECT-CAP",
						CancelledID:   0,
						CancelledRank: lowest.Rank,
						IncomingRank:  incomingRank,
					})
					break
				}

				// Incoming signal outranks the lowest pending — cancel it.
				if err := CancelIBKROrder(lowest.OrderID); err != nil {
					log.Printf("[CANCEL-FAIL] orderId=%d: %v", lowest.OrderID, err)
					sig.ExecStatus = "rejected"
					sig.ExecError = fmt.Sprintf("cancel_failed:orderId=%d", lowest.OrderID)
					updateOrderState(fp, orderState{})
					AddSignal(sig)
					break
				}
				removePendingOrder(lowest.OrderID)
				log.Printf("[REPLACE] cancelled orderId=%d (rank=%.3f) for better rank=%.3f",
					lowest.OrderID, lowest.Rank, incomingRank)
				recordCapEvent(capReplacementEvent{
					Ts:            time.Now(),
					Kind:          "REPLACE",
					CancelledID:   lowest.OrderID,
					CancelledRank: lowest.Rank,
					IncomingRank:  incomingRank,
				})
			}

			// ── Route to bracket (TP+SL both set) or single-leg ──────────────
			var placedOrderID int
			var placedStatus  string

			if sig.TakeProfitPrice > 0 && sig.StopLossPrice > 0 {
				// Bracket path: parent LIMIT + TP LMT + SL STP LMT (OCO group).
				// NEVER fall back to single-leg if bracket fails.
				bracketResult, bracketErr := PlaceBracketIBKROrder(contract, sig, action, absQty, price)
				if bracketErr != nil {
					log.Printf("[BRACKET-REJECT] signal=%s reason=%s", fp, bracketErr.Error())
					sig.ExecStatus = "failed"
					sig.ExecError  = bracketErr.Error()
					updateOrderState(fp, orderState{})
					AddSignal(sig)
					break
				}
				placedOrderID = bracketResult.ParentOrderID
				placedStatus  = bracketResult.Status
				log.Printf("IBKR BRACKET PLACED: parentId=%d tpId=%d slId=%d oca=%s status=%s symbol=%s strike=%.2f rank=%.3f",
					bracketResult.ParentOrderID, bracketResult.TakeProfitOrderID, bracketResult.StopLossOrderID,
					bracketResult.GroupOCA, placedStatus, ticker, strike, incomingRank)
			} else {
				// Single-leg limit order path (no TP/SL provided).
				result, err := PlaceIBKROrder(contract, action, absQty, price)
				if err != nil {
					sig.ExecStatus = "failed"
					sig.ExecError  = err.Error()
					updateOrderState(fp, orderState{})
					AddSignal(sig)
					log.Printf("IBKR ORDER FAILED: %s %dx %s strike=%.2f expiry=%s price=%.4f — %v",
						action, absQty, ticker, strike, sig.ExpirationDate, price, err)
					return
				}
				placedOrderID = result.OrderID
				placedStatus  = result.Status
				if prev.Status == "" || prev.Status != result.Status {
					log.Printf("IBKR ORDER PLACED: orderId=%d status=%s symbol=%s strike=%.2f expiry=%s price=%.4f qty=%d rank=%.3f",
						result.OrderID, result.Status, ticker, strike, sig.ExpirationDate, price, absQty, incomingRank)
				}
			}

			// Commit the real broker state to the dedup map.
			updateOrderState(fp, orderState{OrderID: placedOrderID, Status: placedStatus})

			// Track parent order in pending cap map.
			sig.SignalRank = incomingRank
			addPendingOrder(placedOrderID, incomingRank, sig)

			sig.IBKROrderID = placedOrderID
			sig.ExecStatus  = "submitted"
			AddSignal(sig)

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
