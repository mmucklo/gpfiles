package main

// Phase 19 — Guard circuit breaker reset with market-hours safety gate
//
// Endpoint:
//   POST /api/guard/reset
//
// During market hours (9:30–16:00 ET, Mon–Fri) a reset requires the caller
// to send {"confirm":"RESET_DURING_MARKET_HOURS"} in the JSON body.
// Outside market hours the body can be empty.
//
// The reset is always logged regardless of time.

import (
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"time"
)

// ServeGuardReset handles POST /api/guard/reset.
// During market hours it requires {"confirm":"RESET_DURING_MARKET_HOURS"} in the body.
func (h *ConfigHandler) ServeGuardReset(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
	w.Header().Set("Content-Type", "application/json")

	if r.Method == http.MethodOptions {
		w.WriteHeader(http.StatusNoContent)
		return
	}
	if r.Method != http.MethodPost {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	marketOpen := isMarketOpen()

	if marketOpen {
		var body struct {
			Confirm string `json:"confirm"`
		}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil || body.Confirm != "RESET_DURING_MARKET_HOURS" {
			w.WriteHeader(http.StatusForbidden)
			w.Write([]byte(`{"error":"circuit breaker reset during market hours requires confirm='RESET_DURING_MARKET_HOURS'"}`))
			return
		}
		log.Printf("[GUARD-RESET] WARNING: circuit breaker reset DURING MARKET HOURS by user at %s",
			time.Now().UTC().Format(time.RFC3339))
	}

	// Always log the reset
	log.Printf("[GUARD-RESET] circuit breaker reset at %s", time.Now().UTC().Format(time.RFC3339))

	// Perform the reset
	if h.Guard != nil {
		h.Guard.Reset()
	}

	resp := map[string]interface{}{
		"status":      "reset",
		"warning":     "circuit breaker has been reset",
		"market_open": marketOpen,
		"reset_at":    time.Now().UTC().Format(time.RFC3339),
	}
	if err := json.NewEncoder(w).Encode(resp); err != nil {
		log.Printf("[GUARD-RESET] encode response error: %v", err)
	}
	fmt.Printf("[GUARD-RESET] reset complete (market_open=%v)\n", marketOpen)
}
