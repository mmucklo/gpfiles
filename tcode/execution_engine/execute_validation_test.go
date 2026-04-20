package main

import (
	"encoding/json"
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
	if err.Error() != "expiry is empty — proposal missing expiration_date" {
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
	if err.Error() != "strike is 0 — proposal missing recommended_strike" {
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
	if err.Error() != "quantity is 0 — must be positive" {
		t.Errorf("unexpected error: %v", err)
	}
}
