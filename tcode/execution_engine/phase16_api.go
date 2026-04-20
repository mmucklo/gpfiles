package main

// Phase 16 — Intraday Cockpit API handlers
//
// Endpoints:
//   GET  /api/morning-briefing                      — regime + strategy rec + catalysts
//   POST /api/strategy/select                       — lock session strategy
//   GET  /api/strategy/current                      — current locked strategy
//   GET  /api/trades/proposed                       — pending approval queue
//   POST /api/trades/proposed/:id/execute|skip|adjust
//   GET  /api/trades/ledger?date=YYYY-MM-DD         — trade ledger for date
//   GET  /api/trades/pnl?date=YYYY-MM-DD            — daily P&L + waterfall
//   GET  /api/trades/pnl/strategy-breakdown         — per-strategy P&L
//   GET  /api/regime/current                        — current regime + age
//   POST /api/regime/override                       — user override

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"os/exec"
	"strings"
	"sync"
	"time"

	"github.com/nats-io/nats.go"
)

// ── Proposal Store ─────────────────────────────────────────────────────────

// TradeProposal mirrors the Python proposal dict.
type TradeProposal struct {
	ID                  string          `json:"id"`
	TsCreated           string          `json:"ts_created"`
	TsExpires           string          `json:"ts_expires"`
	Status              string          `json:"status"` // pending|executed|skipped|expired|adjusted
	Strategy            string          `json:"strategy"`
	Direction           string          `json:"direction"`
	Legs                json.RawMessage `json:"legs"`
	EntryPrice          float64         `json:"entry_price"`
	StopPrice           float64         `json:"stop_price"`
	TargetPrice         float64         `json:"target_price"`
	KellyFraction       float64         `json:"kelly_fraction"`
	Quantity            int             `json:"quantity"`
	Confidence          float64         `json:"confidence"`
	RegimeSnapshot      json.RawMessage `json:"regime_snapshot"`
	SignalsContributing json.RawMessage `json:"signals_contributing"`
	RawSignal           json.RawMessage `json:"raw_signal"`
}

// ProposalStore holds pending proposals in-memory.
type ProposalStore struct {
	mu        sync.RWMutex
	proposals map[string]*TradeProposal
	order     []string // insertion order for stable listing
}

var GlobalProposalStore = &ProposalStore{
	proposals: make(map[string]*TradeProposal),
}

func (ps *ProposalStore) Add(p TradeProposal) {
	ps.mu.Lock()
	defer ps.mu.Unlock()
	if _, exists := ps.proposals[p.ID]; !exists {
		ps.order = append([]string{p.ID}, ps.order...) // newest first
	}
	ps.proposals[p.ID] = &p
}

func (ps *ProposalStore) Get(id string) (*TradeProposal, bool) {
	ps.mu.RLock()
	defer ps.mu.RUnlock()
	p, ok := ps.proposals[id]
	return p, ok
}

func (ps *ProposalStore) SetStatus(id, status string) bool {
	ps.mu.Lock()
	defer ps.mu.Unlock()
	if p, ok := ps.proposals[id]; ok {
		p.Status = status
		return true
	}
	return false
}

func (ps *ProposalStore) All() []TradeProposal {
	ps.mu.Lock()
	defer ps.mu.Unlock()
	now := time.Now().UTC()
	var result []TradeProposal
	for _, id := range ps.order {
		p := ps.proposals[id]
		if p == nil {
			continue
		}
		// Auto-expire pending proposals past their TTL
		if p.Status == "pending" {
			if t, err := time.Parse("2006-01-02T15:04:05Z", p.TsExpires); err == nil {
				if now.After(t) {
					p.Status = "expired"
				}
			}
		}
		result = append(result, *p)
	}
	return result
}

// ── NATS proposal subscription ─────────────────────────────────────────────

// StartProposalSubscription subscribes to tsla.alpha.proposals.
// Called from subscriber.go Start() after NATS connect.
func StartProposalSubscription(s *SignalSubscriber) {
	if s.Conn == nil {
		return
	}
	_, err := s.Conn.Subscribe("tsla.alpha.proposals", func(m *nats.Msg) {
		var p TradeProposal
		if err := json.Unmarshal(m.Data, &p); err != nil {
			log.Printf("[PROPOSALS] decode error: %v", err)
			return
		}
		GlobalProposalStore.Add(p)
		log.Printf("[PROPOSALS] received id=%s strategy=%s direction=%s confidence=%.2f",
			p.ID, p.Strategy, p.Direction, p.Confidence)
		// Persist async via Python
		go persistProposalViaPython(p)
	})
	if err != nil {
		log.Printf("[PROPOSALS] subscribe error: %v", err)
		return
	}
	fmt.Println("Proposal Subscriber: Listening for 'tsla.alpha.proposals'...")
}

func persistProposalViaPython(p TradeProposal) {
	raw, err := json.Marshal(p)
	if err != nil {
		return
	}
	cmd := exec.Command("./alpha_engine/venv/bin/python",
		"alpha_engine/data/proposal_store.py", "upsert", string(raw))
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = os.Environ()
	if err := cmd.Run(); err != nil {
		log.Printf("[PROPOSALS] persist failed: %v", err)
	}
}

// ── Morning Briefing ──────────────────────────────────────────────────────

// ServeMorningBriefing handles GET /api/morning-briefing.
func (h *ConfigHandler) ServeMorningBriefing(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	force := r.URL.Query().Get("force") == "1"
	args := []string{"alpha_engine/regime_classifier.py"}
	if force {
		args = append(args, "--force")
	}

	cmd := exec.Command("./alpha_engine/venv/bin/python", args...)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = os.Environ()

	out, err := cmd.Output()
	if err != nil {
		log.Printf("[MORNING-BRIEFING] regime_classifier.py failed: %v", err)
		w.WriteHeader(http.StatusServiceUnavailable)
		w.Write([]byte(`{"regime":"UNCERTAIN","confidence":0,"factors":[],"recommended_strategy":"IRON_CONDOR","fallback_strategy":"WAVE_RIDER","catalysts":[],"error":"regime_classifier unavailable"}`))
		return
	}
	w.Write(out)
}

// ── Strategy Selection ────────────────────────────────────────────────────

// ServeStrategySelect handles POST /api/strategy/select.
func (h *ConfigHandler) ServeStrategySelect(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
	w.Header().Set("Content-Type", "application/json")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	var body struct {
		Strategy string `json:"strategy"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Strategy == "" {
		http.Error(w, `{"error":"strategy required"}`, http.StatusBadRequest)
		return
	}

	validStrategies := map[string]bool{
		"MOMENTUM": true, "IRON_CONDOR": true, "WAVE_RIDER": true,
		"JADE_LIZARD": true, "STRADDLE": true, "GAMMA_SCALP": true,
	}
	if !validStrategies[body.Strategy] {
		http.Error(w, `{"error":"unknown strategy"}`, http.StatusBadRequest)
		return
	}

	now := time.Now().UTC().Format("2006-01-02T15:04:05Z")
	// Persist via Python
	payload := fmt.Sprintf(`{"strategy":%q,"locked_at":%q}`, body.Strategy, now)
	cmd := exec.Command("./alpha_engine/venv/bin/python",
		"alpha_engine/data/proposal_store.py", "set_strategy", payload)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = os.Environ()
	_ = cmd.Run()

	json.NewEncoder(w).Encode(map[string]interface{}{
		"strategy":  body.Strategy,
		"locked_at": now,
		"ok":        true,
	})
}

// ServeStrategyCurrent handles GET /api/strategy/current.
func (h *ConfigHandler) ServeStrategyCurrent(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	cmd := exec.Command("./alpha_engine/venv/bin/python",
		"alpha_engine/data/proposal_store.py", "get_strategy")
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = os.Environ()
	out, err := cmd.Output()
	if err != nil {
		json.NewEncoder(w).Encode(map[string]interface{}{"strategy": nil})
		return
	}
	w.Write(out)
}

// ── Proposals Queue ───────────────────────────────────────────────────────

// ServeTradesProposed handles GET /api/trades/proposed.
func (h *ConfigHandler) ServeTradesProposed(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	filter := r.URL.Query().Get("status")
	all := GlobalProposalStore.All()

	var result []TradeProposal
	for _, p := range all {
		if filter == "" || p.Status == filter {
			result = append(result, p)
		}
	}
	if result == nil {
		result = []TradeProposal{}
	}

	pending, executed, skipped, expired := 0, 0, 0, 0
	for _, p := range all {
		switch p.Status {
		case "pending":
			pending++
		case "executed", "adjusted":
			executed++
		case "skipped":
			skipped++
		case "expired":
			expired++
		}
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"proposals": result,
		"stats": map[string]int{
			"pending":  pending,
			"executed": executed,
			"skipped":  skipped,
			"expired":  expired,
		},
		"updated_at": time.Now().UTC().Format("2006-01-02T15:04:05Z"),
	})
}

// ServeTradeProposalAction handles POST /api/trades/proposed/:id/execute|skip|adjust.
func (h *ConfigHandler) ServeTradeProposalAction(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
	w.Header().Set("Content-Type", "application/json")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	// Parse /api/trades/proposed/:id/:action
	trimmed := strings.TrimPrefix(r.URL.Path, "/api/trades/proposed/")
	parts := strings.SplitN(trimmed, "/", 2)
	if len(parts) < 2 {
		http.Error(w, `{"error":"invalid path"}`, http.StatusBadRequest)
		return
	}
	proposalID := parts[0]
	action := parts[1]

	p, ok := GlobalProposalStore.Get(proposalID)
	if !ok {
		http.Error(w, `{"error":"proposal not found"}`, http.StatusNotFound)
		return
	}
	if p.Status != "pending" {
		w.WriteHeader(http.StatusConflict)
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error":  fmt.Sprintf("proposal already %s", p.Status),
			"status": p.Status,
		})
		return
	}

	// Check expiry
	if t, err := time.Parse("2006-01-02T15:04:05Z", p.TsExpires); err == nil {
		if time.Now().UTC().After(t) {
			GlobalProposalStore.SetStatus(proposalID, "expired")
			w.WriteHeader(http.StatusGone)
			json.NewEncoder(w).Encode(map[string]interface{}{"error": "proposal expired"})
			return
		}
	}

	switch action {
	case "skip":
		GlobalProposalStore.SetStatus(proposalID, "skipped")
		json.NewEncoder(w).Encode(map[string]interface{}{"ok": true, "status": "skipped"})

	case "execute", "adjust":
		var adjustBody struct {
			Quantity     *int     `json:"quantity"`
			StopDistance *float64 `json:"stop_distance"`
			Strategy     *string  `json:"strategy"`
		}
		_ = json.NewDecoder(r.Body).Decode(&adjustBody)

		qty := p.Quantity
		if adjustBody.Quantity != nil && *adjustBody.Quantity > 0 {
			qty = *adjustBody.Quantity
		}

		mode := string(ActiveExecutionMode)
		orderResult, execErr := executeProposalOrder(p, qty, mode)

		finalStatus := action
		if execErr != nil {
			finalStatus = "execute_failed"
			log.Printf("[PROPOSALS] execute failed for %s: %v", proposalID, execErr)
			GlobalProposalStore.SetStatus(proposalID, "execute_failed")
			// Notify operator via Telegram
			if h.Guard != nil && h.Guard.AlertBot != nil {
				contract := fmt.Sprintf("TSLA %s — %s", p.Strategy, p.Direction)
				h.Guard.AlertBot.SendAlert(fmt.Sprintf(
					"EXECUTE FAILED\n%s\nError: %s", contract, execErr.Error(),
				))
			}
		} else {
			GlobalProposalStore.SetStatus(proposalID, finalStatus)
		}

		resp := map[string]interface{}{
			"ok":          execErr == nil,
			"status":      finalStatus,
			"proposal_id": proposalID,
		}
		if orderResult != nil {
			resp["order_result"] = orderResult
		}
		if execErr != nil {
			resp["error"] = execErr.Error()
			w.WriteHeader(http.StatusInternalServerError)
		}
		json.NewEncoder(w).Encode(resp)

	default:
		http.Error(w, `{"error":"unknown action"}`, http.StatusBadRequest)
	}
}

func executeProposalOrder(p *TradeProposal, qty int, mode string) (map[string]interface{}, error) {
	var rawSig map[string]interface{}
	if len(p.RawSignal) > 0 {
		_ = json.Unmarshal(p.RawSignal, &rawSig)
	}

	symbol := "TSLA"
	if v, ok := rawSig["ticker"].(string); ok && v != "" {
		symbol = v
	}
	strikeVal := 0.0
	if v, ok := rawSig["recommended_strike"].(float64); ok {
		strikeVal = v
	}
	if strikeVal == 0 {
		strikeVal = p.EntryPrice
	}
	strike := fmt.Sprintf("%.2f", strikeVal)
	expiry := ""
	if v, ok := rawSig["expiration_date"].(string); ok {
		expiry = v
	}
	optType := ""
	if v, ok := rawSig["option_type"].(string); ok {
		optType = v
	}
	// Fall back to "CALL" only if option_type is explicitly absent from raw signal
	// AND the proposal legs provide a type — otherwise the validation below will reject.
	if optType == "" {
		if len(p.Legs) > 2 {
			// Try to extract from legs JSON
			var legs []map[string]interface{}
			if json.Unmarshal(p.Legs, &legs) == nil && len(legs) > 0 {
				if t, ok := legs[0]["type"].(string); ok && t != "" {
					optType = t
				}
			}
		}
	}

	// Validate required fields before invoking ibkr_order.py
	if strikeVal <= 0 {
		return nil, fmt.Errorf("[ORDER-REJECT] strike is %.2f — must be positive", strikeVal)
	}
	if expiry == "" {
		return nil, fmt.Errorf("[ORDER-REJECT] expiry is empty — proposal missing expiration_date")
	}
	if qty <= 0 {
		return nil, fmt.Errorf("[ORDER-REJECT] quantity is %d — must be positive", qty)
	}
	if optType == "" {
		return nil, fmt.Errorf("[ORDER-REJECT] option_type is empty")
	}
	if _, err := time.Parse("2006-01-02", expiry); err != nil {
		return nil, fmt.Errorf("[ORDER-REJECT] expiry '%s' is not a valid date: %w", expiry, err)
	}

	limitPrice := fmt.Sprintf("%.4f", p.EntryPrice)
	tp := fmt.Sprintf("%.4f", p.TargetPrice)
	sl := fmt.Sprintf("%.4f", p.StopPrice)
	qtyStr := fmt.Sprintf("%d", qty)
	clientID := AllocateClientID()

	args := []string{
		"alpha_engine/ingestion/ibkr_order.py", "place",
		"--symbol", symbol, "--contract", optType,
		"--strike", strike, "--expiry", expiry,
		"--action", "BUY", "--quantity", qtyStr,
		"--limit-price", limitPrice,
		"--take-profit", tp, "--stop-loss", sl,
		"--mode", mode, "--client-id", fmt.Sprintf("%d", clientID),
	}

	cmd := exec.Command("./alpha_engine/venv/bin/python", args...)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = os.Environ()

	log.Printf("[PROPOSALS] executing: python %s", strings.Join(args, " "))

	out, err := cmd.Output()
	log.Printf("[PROPOSALS] ibkr_order output: %s", strings.TrimSpace(string(out)))
	if err != nil {
		return nil, fmt.Errorf("ibkr_order.py failed: %w", err)
	}

	var result map[string]interface{}
	if err := json.Unmarshal(out, &result); err != nil {
		return nil, fmt.Errorf("ibkr_order.py output parse error: %w", err)
	}
	if errMsg, ok := result["error"].(string); ok && errMsg != "" {
		return result, fmt.Errorf("ibkr_order.py: %s", errMsg)
	}
	return result, nil
}

// ── Trade Ledger ──────────────────────────────────────────────────────────

// ServeTradeLedger handles GET /api/trades/ledger?date=YYYY-MM-DD.
func (h *ConfigHandler) ServeTradeLedger(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	date := r.URL.Query().Get("date")
	if date == "" {
		date = time.Now().UTC().Format("2006-01-02")
	}

	cmd := exec.Command("./alpha_engine/venv/bin/python",
		"alpha_engine/data/proposal_store.py", "ledger", date)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = os.Environ()
	out, err := cmd.Output()
	if err != nil {
		json.NewEncoder(w).Encode(map[string]interface{}{"date": date, "trades": []interface{}{}})
		return
	}
	w.Write(out)
}

// ── P&L ──────────────────────────────────────────────────────────────────

// ServeTradePnL handles GET /api/trades/pnl?date=YYYY-MM-DD.
func (h *ConfigHandler) ServeTradePnL(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	date := r.URL.Query().Get("date")
	if date == "" {
		date = time.Now().UTC().Format("2006-01-02")
	}

	cmd := exec.Command("./alpha_engine/venv/bin/python",
		"alpha_engine/data/proposal_store.py", "pnl", date)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = os.Environ()
	out, err := cmd.Output()
	if err != nil {
		// Return sensible empty state
		json.NewEncoder(w).Encode(map[string]interface{}{
			"date":             date,
			"total_pnl":        0,
			"daily_target":     10000,
			"target_pct":       0,
			"daily_loss_limit": -2500,
			"loss_used_pct":    0,
			"circuit_broken":   false,
			"winners":          0,
			"losers":           0,
			"waterfall":        []interface{}{},
			"updated_at":       time.Now().UTC().Format("2006-01-02T15:04:05Z"),
		})
		return
	}
	w.Write(out)
}

// ServeTradePnLStrategyBreakdown handles GET /api/trades/pnl/strategy-breakdown.
func (h *ConfigHandler) ServeTradePnLStrategyBreakdown(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	date := r.URL.Query().Get("date")
	if date == "" {
		date = time.Now().UTC().Format("2006-01-02")
	}

	cmd := exec.Command("./alpha_engine/venv/bin/python",
		"alpha_engine/data/proposal_store.py", "strategy_breakdown", date)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = os.Environ()
	out, err := cmd.Output()
	if err != nil {
		json.NewEncoder(w).Encode(map[string]interface{}{"date": date, "strategies": []interface{}{}})
		return
	}
	w.Write(out)
}

// ── Regime ────────────────────────────────────────────────────────────────

// ServeRegimeCurrent handles GET /api/regime/current.
func (h *ConfigHandler) ServeRegimeCurrent(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	cmd := exec.Command("./alpha_engine/venv/bin/python", "alpha_engine/regime_classifier.py")
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = os.Environ()
	out, err := cmd.Output()
	if err != nil {
		w.WriteHeader(http.StatusServiceUnavailable)
		w.Write([]byte(`{"regime":"UNCERTAIN","confidence":0,"error":"classifier unavailable"}`))
		return
	}
	w.Write(out)
}

// ServeRegimeOverride handles POST /api/regime/override.
func (h *ConfigHandler) ServeRegimeOverride(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	if r.Method != http.MethodPost {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	var body struct {
		Regime string `json:"regime"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Regime == "" {
		http.Error(w, `{"error":"regime required"}`, http.StatusBadRequest)
		return
	}

	valid := map[string]bool{
		"TRENDING": true, "FLAT": true, "CHOPPY": true,
		"EVENT_DRIVEN": true, "UNCERTAIN": true,
	}
	if !valid[body.Regime] {
		http.Error(w, `{"error":"unknown regime"}`, http.StatusBadRequest)
		return
	}

	payload := fmt.Sprintf(`{"regime":%q}`, body.Regime)
	cmd := exec.Command("./alpha_engine/venv/bin/python",
		"alpha_engine/data/proposal_store.py", "override_regime", payload)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = os.Environ()
	_ = cmd.Run()

	json.NewEncoder(w).Encode(map[string]interface{}{
		"ok":     true,
		"regime": body.Regime,
	})
}

