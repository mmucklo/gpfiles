package main

import (
	"encoding/json"
	"strings"
	"testing"
)

func makeProposal(rawSignal map[string]interface{}) *TradeProposal {
	raw, _ := json.Marshal(rawSignal)
	return &TradeProposal{
		ID:          "test-id",
		EntryPrice:  5.0,
		TargetPrice: 7.0,
		StopPrice:   3.0,
		RawSignal:   json.RawMessage(raw),
	}
}

func TestExecuteValidation_EmptyExpiry(t *testing.T) {
	p := makeProposal(map[string]interface{}{
		"recommended_strike": 400.0,
		"option_type":        "CALL",
		// expiration_date deliberately absent
	})
	_, err := executeProposalOrder(p, 1, "paper")
	if err == nil {
		t.Fatal("expected error for missing expiry, got nil")
	}
	if !strings.Contains(err.Error(), "expiry is empty") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestExecuteValidation_ZeroStrike(t *testing.T) {
	p := makeProposal(map[string]interface{}{
		"expiration_date": "2026-04-25",
		"option_type":     "CALL",
		// recommended_strike absent → strikeVal stays 0
	})
	p.EntryPrice = 0 // also zero so fallback stays 0
	_, err := executeProposalOrder(p, 1, "paper")
	if err == nil {
		t.Fatal("expected error for zero strike, got nil")
	}
	if !strings.Contains(err.Error(), "strike") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestExecuteValidation_NegativeStrike(t *testing.T) {
	p := makeProposal(map[string]interface{}{
		"recommended_strike": -5.0,
		"expiration_date":    "2026-04-25",
		"option_type":        "CALL",
	})
	p.EntryPrice = -5.0
	_, err := executeProposalOrder(p, 1, "paper")
	if err == nil {
		t.Fatal("expected error for negative strike, got nil")
	}
	if !strings.Contains(err.Error(), "strike") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestExecuteValidation_ZeroQty(t *testing.T) {
	p := makeProposal(map[string]interface{}{
		"recommended_strike": 400.0,
		"expiration_date":    "2026-04-25",
		"option_type":        "CALL",
	})
	_, err := executeProposalOrder(p, 0, "paper")
	if err == nil {
		t.Fatal("expected error for zero qty, got nil")
	}
	if !strings.Contains(err.Error(), "quantity") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestExecuteValidation_EmptyOptionType(t *testing.T) {
	p := makeProposal(map[string]interface{}{
		"recommended_strike": 400.0,
		"expiration_date":    "2026-04-25",
		// option_type deliberately absent
	})
	_, err := executeProposalOrder(p, 1, "paper")
	if err == nil {
		t.Fatal("expected error for empty option_type, got nil")
	}
	if !strings.Contains(err.Error(), "option_type is empty") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestExecuteValidation_InvalidDateFormat(t *testing.T) {
	p := makeProposal(map[string]interface{}{
		"recommended_strike": 400.0,
		"expiration_date":    "not-a-date",
		"option_type":        "CALL",
	})
	_, err := executeProposalOrder(p, 1, "paper")
	if err == nil {
		t.Fatal("expected error for invalid date, got nil")
	}
	if !strings.Contains(err.Error(), "not a valid date") {
		t.Errorf("unexpected error: %v", err)
	}
}

func TestExecuteValidation_ValidInputs_NoValidationError(t *testing.T) {
	// Valid inputs should not fail on validation (may fail later trying to spawn python)
	p := makeProposal(map[string]interface{}{
		"recommended_strike": 400.0,
		"expiration_date":    "2026-04-25",
		"option_type":        "CALL",
	})
	_, err := executeProposalOrder(p, 2, "paper")
	// Should not get a validation error — may get ibkr_order.py not found error
	if err != nil && (strings.Contains(err.Error(), "[ORDER-REJECT]") ||
		strings.Contains(err.Error(), "strike") && strings.Contains(err.Error(), "must be positive")) {
		t.Errorf("got unexpected validation error for valid inputs: %v", err)
	}
}
