package main

import (
	"testing"
)

func TestPaperPortfolio_OpenClose(t *testing.T) {
	p := NewPaperPortfolio(100000.0)
	
	pos := Position{
		Ticker:     "TSLA",
		OptionType: "CALL",
		Strike:     200.0,
		Expiry:     "2026-03-20",
		EntryPrice: 5.0,
		Quantity:   10,
	}

	// Open position
	err := p.OpenPosition(pos)
	if err != nil {
		t.Fatalf("Failed to open position: %v", err)
	}

	if p.Cash != 100000.0-(10*5.0*100) {
		t.Errorf("Expected cash $95000, got $%.2f", p.Cash)
	}

	if p.MaintenanceMargin < 0 {
		t.Errorf("Expected non-negative margin, got $%.2f", p.MaintenanceMargin)
	}

	// Close position
	pnl, err := p.ClosePosition("TSLA", "CALL", "2026-03-20", 200.0, 6.0, 10)
	if err != nil {
		t.Fatalf("Failed to close position: %v", err)
	}

	if pnl != 1000.0 {
		t.Errorf("Expected PnL $1000, got $%.2f", pnl)
	}

	if p.Cash != 101000.0 {
		t.Errorf("Expected cash $101000, got $%.2f", p.Cash)
	}

	if len(p.Positions) != 0 {
		t.Errorf("Expected 0 positions, got %d", len(p.Positions))
	}
}

func TestComplianceGuard_WashSale(t *testing.T) {
	g := NewComplianceGuard(100000.0)
	sig := "TSLA_CALL_2026-03-20_200.00"

	// Record a loss
	g.RecordTradeOutcome(sig, -500.0, true)

	// Check wash sale rule
	err := g.CheckWashSaleRule(sig)
	if err == nil {
		t.Error("Expected wash sale block, got nil")
	}

	// Check different signature (should be fine)
	err = g.CheckWashSaleRule("AAPL_CALL_2026-03-20_150.00")
	if err != nil {
		t.Errorf("Expected no block for different signature, got %v", err)
	}
}
