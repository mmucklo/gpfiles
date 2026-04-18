package main

// Phase 17 — Intraday Execution Engine API handlers
//
// Endpoints:
//   GET /api/bars/latest          — last 20 1-min TSLA bars + ATR + volume ratio
//   GET /api/circuit-breaker      — daily P&L state, consecutive losses, pause status
//   GET /api/positions/managed    — open managed positions (stop levels, countdowns)
//   POST /api/positions/managed/:id/close — manual close

import (
	"encoding/json"
	"net/http"
	"os/exec"
	"strings"
	"time"
)

// ── /api/bars/latest ─────────────────────────────────────────────────────────

// ServeBarsLatest handles GET /api/bars/latest.
// Delegates to Python realtime_bars module via subprocess.
func (h *ConfigHandler) ServeBarsLatest(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	cmd := exec.Command("./alpha_engine/venv/bin/python", "-c", `
import sys, json
sys.path.insert(0, 'alpha_engine')
from ingestion.realtime_bars import get_latest
print(json.dumps(get_latest()))
`)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	out, err := cmd.Output()
	if err != nil {
		// Return empty-but-valid response so UI renders gracefully
		json.NewEncoder(w).Encode(map[string]interface{}{
			"bars":       []interface{}{},
			"indicators": map[string]interface{}{"atr": 0, "volume_ratio": 1, "vwap": 0, "bar_count": 0},
			"updated_at": time.Now().UTC().Format("2006-01-02T15:04:05Z"),
			"error":      "realtime_bars not initialized",
		})
		return
	}
	w.Write(out)
}

// ── /api/circuit-breaker ──────────────────────────────────────────────────────

// ServeCircuitBreaker handles GET /api/circuit-breaker.
func (h *ConfigHandler) ServeCircuitBreaker(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	cmd := exec.Command("./alpha_engine/venv/bin/python", "-c", `
import sys, json
sys.path.insert(0, 'alpha_engine')
from circuit_breaker import evaluate
print(json.dumps(evaluate()))
`)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	out, err := cmd.Output()
	if err != nil {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"status":              "active",
			"daily_pnl":          0,
			"consecutive_losses": 0,
			"remaining_pause_sec": 0,
			"error":              "circuit_breaker unavailable",
		})
		return
	}
	w.Write(out)
}

// ── /api/positions/managed ────────────────────────────────────────────────────

// ServeManagedPositions handles GET /api/positions/managed.
func (h *ConfigHandler) ServeManagedPositions(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	cmd := exec.Command("./alpha_engine/venv/bin/python", "-c", `
import sys, json
sys.path.insert(0, 'alpha_engine')
from stop_manager import get_open_positions
from ingestion.realtime_bars import get_latest
bars_data = get_latest()
positions = get_open_positions()
print(json.dumps({"positions": positions, "bars": bars_data["bars"], "indicators": bars_data["indicators"]}))
`)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	out, err := cmd.Output()
	if err != nil {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"positions":  []interface{}{},
			"bars":       []interface{}{},
			"indicators": map[string]interface{}{},
		})
		return
	}
	w.Write(out)
}

// ServeManagedPositionClose handles POST /api/positions/managed/:id/close.
func (h *ConfigHandler) ServeManagedPositionClose(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	if r.Method != "POST" {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	// Extract trade_id from path: /api/positions/managed/{id}/close
	path := r.URL.Path
	parts := strings.Split(strings.Trim(path, "/"), "/")
	// parts: ["api","positions","managed","{id}","close"]
	if len(parts) < 5 {
		http.Error(w, `{"error":"missing trade_id"}`, http.StatusBadRequest)
		return
	}
	tradeID := parts[3]

	var body struct {
		ExitPrice float64 `json:"exit_price"`
	}
	_ = json.NewDecoder(r.Body).Decode(&body)

	exitPrice := body.ExitPrice
	if exitPrice == 0 {
		// Fall back to latest bar close
		exitPrice = 0
	}

	cmd := exec.Command("./alpha_engine/venv/bin/python", "-c", `
import sys, json
sys.path.insert(0, 'alpha_engine')
from stop_manager import manual_close
from ingestion.realtime_bars import get_latest_close
trade_id = int(sys.argv[1])
exit_price = float(sys.argv[2]) if sys.argv[2] != "0" else get_latest_close()
ok = manual_close(trade_id, exit_price)
print(json.dumps({"ok": ok, "trade_id": trade_id, "exit_price": exit_price}))
`, tradeID, "0")
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	out, err := cmd.Output()
	if err != nil {
		http.Error(w, `{"error":"manual close failed"}`, http.StatusInternalServerError)
		return
	}
	w.Write(out)
}
