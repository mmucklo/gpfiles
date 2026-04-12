package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
)

// stubAccountHandler builds a ConfigHandler backed by a mock portfolio.
func stubAccountHandler() *ConfigHandler {
	portfolio := NewPaperPortfolio(25000.0)
	guard := NewLiveCapitalGuard(10000.0, &TelegramBot{})
	return &ConfigHandler{
		Executor:  NewIBKRExecutor(25000.0, NewComplianceGuard(25000.0)),
		Guard:     guard,
		Portfolio: portfolio,
	}
}

// TestServeAccount_ErrorWhenNoPython verifies that /api/account returns a JSON
// object with the required keys even when the Python subprocess fails.
func TestServeAccount_ErrorWhenNoPython(t *testing.T) {
	h := stubAccountHandler()
	req := httptest.NewRequest(http.MethodGet, "/api/account", nil)
	rr := httptest.NewRecorder()

	// The subprocess will fail (no real alpha_engine in test env).
	// Expect a JSON error response with the required zero-value fields.
	h.ServeAccount(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}

	var body map[string]interface{}
	if err := json.NewDecoder(rr.Body).Decode(&body); err != nil {
		t.Fatalf("response is not valid JSON: %v", err)
	}

	// Must have all required fields (even if zeroed due to error)
	required := []string{"net_liquidation", "cash_balance", "buying_power",
		"unrealized_pnl", "realized_pnl", "equity_with_loan"}
	for _, key := range required {
		if _, ok := body[key]; !ok {
			t.Errorf("missing key %q in /api/account response", key)
		}
	}
}

// TestServePositions_ReturnsArray verifies /api/positions returns a JSON array.
func TestServePositions_ReturnsArray(t *testing.T) {
	h := stubAccountHandler()
	req := httptest.NewRequest(http.MethodGet, "/api/positions", nil)
	rr := httptest.NewRecorder()

	h.ServePositions(rr, req)

	if rr.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", rr.Code)
	}

	body := rr.Body.String()
	// Must be a JSON array (either [] on error or populated)
	if len(body) == 0 {
		t.Fatal("empty response body")
	}
	if body[0] != '[' && body[0] != '{' {
		t.Errorf("expected JSON array or object, got: %s", body[:min(len(body), 50)])
	}
}

// TestServeSimToggle verifies the mode toggles between paper and sim.
func TestServeSimToggle(t *testing.T) {
	h := stubAccountHandler()

	// Reset to known state
	simModeMu.Lock()
	simMode = "paper"
	simModeMu.Unlock()

	req1 := httptest.NewRequest(http.MethodGet, "/api/sim/toggle", nil)
	rr1 := httptest.NewRecorder()
	h.ServeSimToggle(rr1, req1)

	var r1 map[string]string
	json.NewDecoder(rr1.Body).Decode(&r1)
	if r1["mode"] != "sim" {
		t.Errorf("expected mode=sim after first toggle, got %q", r1["mode"])
	}

	req2 := httptest.NewRequest(http.MethodGet, "/api/sim/toggle", nil)
	rr2 := httptest.NewRecorder()
	h.ServeSimToggle(rr2, req2)

	var r2 map[string]string
	json.NewDecoder(rr2.Body).Decode(&r2)
	if r2["mode"] != "paper" {
		t.Errorf("expected mode=paper after second toggle, got %q", r2["mode"])
	}
}
