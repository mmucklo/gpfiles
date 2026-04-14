package main

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

// ── parseContractKey ──────────────────────────────────────────────────────────

func TestParseContractKey_Valid(t *testing.T) {
	sym, ct, exp, strike, err := parseContractKey("TSLA_CALL_2026-04-13_365")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if sym != "TSLA" || ct != "CALL" || exp != "2026-04-13" || strike != 365.0 {
		t.Fatalf("parsed wrong: sym=%q ct=%q exp=%q strike=%f", sym, ct, exp, strike)
	}
}

func TestParseContractKey_WithDecimalStrike(t *testing.T) {
	_, _, _, strike, err := parseContractKey("TSLA_PUT_2026-05-16_362.5")
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if strike != 362.5 {
		t.Fatalf("expected 362.5 got %f", strike)
	}
}

func TestParseContractKey_TooFewParts(t *testing.T) {
	_, _, _, _, err := parseContractKey("TSLA_CALL_2026-04-13")
	if err == nil {
		t.Fatal("expected error for malformed key")
	}
}

func TestParseContractKey_BadStrike(t *testing.T) {
	_, _, _, _, err := parseContractKey("TSLA_CALL_2026-04-13_notanumber")
	if err == nil {
		t.Fatal("expected error for non-numeric strike")
	}
}

// ── /api/orders/cancel ────────────────────────────────────────────────────────

// makeTestHandler builds a ConfigHandler suitable for unit tests (no live deps).
func makeTestHandler() *ConfigHandler {
	return &ConfigHandler{
		Portfolio: NewPaperPortfolio(100_000),
	}
}

func TestServeOrdersCancel_MethodNotAllowed(t *testing.T) {
	h := makeTestHandler()
	req := httptest.NewRequest("GET", "/api/orders/cancel", nil)
	w := httptest.NewRecorder()
	h.ServeOrdersCancel(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405 got %d", w.Code)
	}
}

func TestServeOrdersCancel_SimulationMode(t *testing.T) {
	orig := ActiveExecutionMode
	ActiveExecutionMode = ModeSimulation
	defer func() { ActiveExecutionMode = orig }()

	h := makeTestHandler()
	body := `{"order_id": 42}`
	req := httptest.NewRequest("POST", "/api/orders/cancel", strings.NewReader(body))
	w := httptest.NewRecorder()
	h.ServeOrdersCancel(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 in SIMULATION mode, got %d", w.Code)
	}
	if !strings.Contains(w.Body.String(), "SIMULATION") {
		t.Fatalf("expected SIMULATION error message, got: %s", w.Body.String())
	}
}

func TestServeOrdersCancel_MissingOrderID(t *testing.T) {
	orig := ActiveExecutionMode
	ActiveExecutionMode = ModeIBKRPaper
	defer func() { ActiveExecutionMode = orig }()

	h := makeTestHandler()
	body := `{"order_id": 0}`
	req := httptest.NewRequest("POST", "/api/orders/cancel", strings.NewReader(body))
	w := httptest.NewRecorder()
	h.ServeOrdersCancel(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for zero order_id, got %d", w.Code)
	}
}

func TestServeOrdersCancel_InvalidJSON(t *testing.T) {
	orig := ActiveExecutionMode
	ActiveExecutionMode = ModeIBKRPaper
	defer func() { ActiveExecutionMode = orig }()

	h := makeTestHandler()
	req := httptest.NewRequest("POST", "/api/orders/cancel", strings.NewReader("not-json"))
	w := httptest.NewRecorder()
	h.ServeOrdersCancel(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for invalid JSON, got %d", w.Code)
	}
}

// ── /api/positions/close ──────────────────────────────────────────────────────

func TestServePositionsClose_MethodNotAllowed(t *testing.T) {
	h := makeTestHandler()
	req := httptest.NewRequest("GET", "/api/positions/close", nil)
	w := httptest.NewRecorder()
	h.ServePositionsClose(w, req)
	if w.Code != http.StatusMethodNotAllowed {
		t.Fatalf("expected 405 got %d", w.Code)
	}
}

func TestServePositionsClose_SimulationMode(t *testing.T) {
	orig := ActiveExecutionMode
	ActiveExecutionMode = ModeSimulation
	defer func() { ActiveExecutionMode = orig }()

	h := makeTestHandler()
	body := `{"contract_key":"TSLA_CALL_2026-04-13_365","quantity":10,"market_open_if_closed":true}`
	req := httptest.NewRequest("POST", "/api/positions/close", strings.NewReader(body))
	w := httptest.NewRecorder()
	h.ServePositionsClose(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 in SIMULATION mode, got %d", w.Code)
	}
}

func TestServePositionsClose_MissingContractKey(t *testing.T) {
	orig := ActiveExecutionMode
	ActiveExecutionMode = ModeIBKRPaper
	defer func() { ActiveExecutionMode = orig }()

	h := makeTestHandler()
	body := `{"contract_key":"","quantity":10}`
	req := httptest.NewRequest("POST", "/api/positions/close", strings.NewReader(body))
	w := httptest.NewRecorder()
	h.ServePositionsClose(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for empty contract_key, got %d", w.Code)
	}
}

func TestServePositionsClose_ZeroQuantity(t *testing.T) {
	orig := ActiveExecutionMode
	ActiveExecutionMode = ModeIBKRPaper
	defer func() { ActiveExecutionMode = orig }()

	h := makeTestHandler()
	body := `{"contract_key":"TSLA_CALL_2026-04-13_365","quantity":0}`
	req := httptest.NewRequest("POST", "/api/positions/close", strings.NewReader(body))
	w := httptest.NewRecorder()
	h.ServePositionsClose(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for zero quantity, got %d", w.Code)
	}
}

func TestServePositionsClose_MalformedContractKey(t *testing.T) {
	orig := ActiveExecutionMode
	ActiveExecutionMode = ModeIBKRPaper
	defer func() { ActiveExecutionMode = orig }()

	h := makeTestHandler()
	body := `{"contract_key":"TSLA_CALL_notvalid","quantity":5}`
	req := httptest.NewRequest("POST", "/api/positions/close", strings.NewReader(body))
	w := httptest.NewRecorder()
	h.ServePositionsClose(w, req)
	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400 for malformed contract_key, got %d", w.Code)
	}
}

// ── auditLog marshalling ──────────────────────────────────────────────────────

func TestAuditLog_DoesNotPanic(t *testing.T) {
	// Just confirm auditLog doesn't panic for various input types
	auditLog("/api/orders/cancel", "IBKR_PAPER", map[string]int{"order_id": 42}, map[string]string{"status": "Cancelled"})
	auditLog("/api/positions/close", "IBKR_PAPER", nil, nil)

	// Unencodable input should not panic (json.Marshal silences errors)
	type cyclicSafe struct{ Val int }
	auditLog("/test", "PAPER", cyclicSafe{1}, cyclicSafe{2})
}

// ── JSON response shape sanity ────────────────────────────────────────────────

func TestCancelOrderResult_JSONRoundtrip(t *testing.T) {
	r := CancelOrderResult{
		OrderID:      101,
		Status:       "Cancelled",
		OcaCancelled: []int{102, 103},
		Timestamp:    "2026-04-13T14:00:00Z",
	}
	b, err := json.Marshal(r)
	if err != nil {
		t.Fatalf("marshal: %v", err)
	}
	var out CancelOrderResult
	if err := json.Unmarshal(b, &out); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	if out.OrderID != 101 || len(out.OcaCancelled) != 2 {
		t.Fatalf("roundtrip mismatch: %+v", out)
	}
}

func TestClosePositionResult_JSONRoundtrip(t *testing.T) {
	r := ClosePositionResult{
		OrderID:      77,
		Status:       "PendingSubmit",
		ScheduledFor: "2026-04-14T13:30:00Z",
		Timestamp:    "2026-04-13T20:00:00Z",
	}
	b, _ := json.Marshal(r)

	// POST body simulation: wrap in httptest
	req := httptest.NewRequest("POST", "/api/positions/close",
		bytes.NewReader([]byte(`{"contract_key":"TSLA_CALL_2026-04-13_365","quantity":5}`)),
	)
	_ = req // Just confirm the struct fields match the expected JSON shape

	var out ClosePositionResult
	json.Unmarshal(b, &out)
	if out.ScheduledFor != "2026-04-14T13:30:00Z" {
		t.Fatalf("scheduled_for roundtrip failed: %q", out.ScheduledFor)
	}
}
