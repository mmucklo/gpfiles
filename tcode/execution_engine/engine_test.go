package main

import (
	"testing"
)

func testPricingEngine(t *testing.T) {
	pe := &PricingEngine{}
	price := pe.CallPrice(200.0, 210.0, 0.08, 0.04, 0.50)
	if price < 7.0 || price > 8.0 {
		t.Errorf("Unexpected CallPrice: %f (Expected ~7.47)", price)
	}
}

func testLiveCapitalGuard(t *testing.T) {
	bot := &TelegramBot{}
	guard := NewLiveCapitalGuard(100.0, bot)
	if !guard.CheckSafety(50.0)  { t.Error("Guard should allow profit swing") }
	if !guard.CheckSafety(-60.0) { t.Error("Guard should allow small loss swing") }
	if guard.CheckSafety(-100.0) { t.Error("Guard should have triggered kill-switch") }
	if !guard.KillSwitch         { t.Error("KillSwitch should be true") }
	if guard.CheckSafety(-10.0)  { t.Error("Guard should block after kill-switch") }
}

func testIBKRExecutor(t *testing.T) {
	compliance := NewComplianceGuard(25000.0)
	executor := NewIBKRExecutor(25000.0, compliance)
	executor.ExecuteOrder("TEST", "CALL", 200.0, "2026-12-19", 1, 5.0)
	_ = executor.AccountBalance
}

func TestAll(t *testing.T) {
	t.Run("PricingEngine", testPricingEngine)
	t.Run("LiveCapitalGuard", testLiveCapitalGuard)
	t.Run("IBKRExecutor", testIBKRExecutor)
}
