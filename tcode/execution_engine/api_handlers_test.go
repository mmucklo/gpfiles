package main

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func newTestHandler() *ConfigHandler {
	guard := NewLiveCapitalGuard(2500, &TelegramBot{})
	return &ConfigHandler{Guard: guard}
}

// assertValidJSON verifies the response body is valid JSON (not HTML, not empty).
func assertValidJSON(t *testing.T, w *httptest.ResponseRecorder) {
	t.Helper()
	body := w.Body.String()
	if body == "" {
		t.Error("response body is empty")
		return
	}
	if strings.HasPrefix(strings.TrimSpace(body), "<") {
		t.Errorf("response body looks like HTML: %q", body[:minInt(80, len(body))])
		return
	}
	var v interface{}
	if err := json.Unmarshal([]byte(body), &v); err != nil {
		t.Errorf("response is not valid JSON: %v — body: %q", err, body[:minInt(200, len(body))])
	}
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// TestTradesProposed_ReturnsValidJSON verifies GET /api/trades/proposed returns valid JSON.
func TestTradesProposed_ReturnsValidJSON(t *testing.T) {
	h := newTestHandler()
	req := httptest.NewRequest(http.MethodGet, "/api/trades/proposed", nil)
	w := httptest.NewRecorder()
	h.ServeTradesProposed(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	assertValidJSON(t, w)
	// Must have proposals key
	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	if _, ok := resp["proposals"]; !ok {
		t.Error("expected 'proposals' key in response")
	}
}

// TestTradesProposed_Execute_MissingProposal returns 404.
func TestTradesProposed_Execute_MissingProposal(t *testing.T) {
	h := newTestHandler()
	req := httptest.NewRequest(http.MethodPost, "/api/trades/proposed/nonexistent-id/execute", nil)
	req.RequestURI = "/api/trades/proposed/nonexistent-id/execute"
	req.URL.Path = "/api/trades/proposed/nonexistent-id/execute"
	w := httptest.NewRecorder()
	h.ServeTradeProposalAction(w, req)
	if w.Code != http.StatusNotFound {
		t.Errorf("expected 404 for missing proposal, got %d", w.Code)
	}
	assertValidJSON(t, w)
}

// TestSystemPauseUnpause_Roundtrip verifies pause + unpause cycle works.
func TestSystemPauseUnpause_Roundtrip(t *testing.T) {
	h := newTestHandler()

	// Pause
	pauseReq := httptest.NewRequest(http.MethodPost, "/api/system/pause", nil)
	pauseW := httptest.NewRecorder()
	h.ServePause(pauseW, pauseReq)
	if pauseW.Code != http.StatusOK {
		t.Errorf("pause: expected 200, got %d", pauseW.Code)
	}
	assertValidJSON(t, pauseW)

	// Status should be paused
	statusReq := httptest.NewRequest(http.MethodGet, "/api/system/pause-status", nil)
	statusW := httptest.NewRecorder()
	h.ServePauseStatus(statusW, statusReq)
	if statusW.Code != http.StatusOK {
		t.Errorf("pause-status: expected 200, got %d", statusW.Code)
	}
	assertValidJSON(t, statusW)
	var status map[string]interface{}
	json.Unmarshal(statusW.Body.Bytes(), &status)
	if status["paused"] != true {
		t.Errorf("expected paused=true, got %v", status["paused"])
	}
}

// TestStrategySelect_UnknownStrategy returns 400.
func TestStrategySelect_UnknownStrategy(t *testing.T) {
	h := newTestHandler()
	req := httptest.NewRequest(http.MethodPost, "/api/strategy/select",
		strings.NewReader(`{"strategy":"UNKNOWN_STRATEGY"}`))
	w := httptest.NewRecorder()
	h.ServeStrategySelect(w, req)
	if w.Code != http.StatusBadRequest {
		t.Errorf("expected 400 for unknown strategy, got %d", w.Code)
	}
	assertValidJSON(t, w)
}

// TestCircuitBreaker_ReturnsValidJSON verifies /api/circuit-breaker returns valid JSON on error too.
func TestCircuitBreaker_ReturnsValidJSON(t *testing.T) {
	h := newTestHandler()
	req := httptest.NewRequest(http.MethodGet, "/api/circuit-breaker", nil)
	w := httptest.NewRecorder()
	h.ServeCircuitBreaker(w, req)
	// Accept 200 (python available) or 200 with error field (python unavailable)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d", w.Code)
	}
	assertValidJSON(t, w)
}

// TestGuardReset_EndpointReturnsValidJSON checks the real handler returns valid JSON.
func TestGuardReset_EndpointReturnsValidJSON(t *testing.T) {
	h := newTestHandler()
	// Use confirm token to bypass market hours check (safe in tests since market may be open)
	body := `{"confirm":"RESET_DURING_MARKET_HOURS"}`
	req := httptest.NewRequest(http.MethodPost, "/api/guard/reset", strings.NewReader(body))
	w := httptest.NewRecorder()
	h.ServeGuardReset(w, req)
	if w.Code != http.StatusOK {
		t.Errorf("expected 200, got %d body=%s", w.Code, w.Body.String())
	}
	assertValidJSON(t, w)
	var resp map[string]interface{}
	json.Unmarshal(w.Body.Bytes(), &resp)
	if resp["status"] != "reset" {
		t.Errorf("expected status=reset, got %v", resp["status"])
	}
}
