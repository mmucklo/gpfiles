package main

import (
	"fmt"
	"sync"
	"time"
)

// DayTrade represents a single day trade (Buy and Sell on the same day).
type DayTrade struct {
	Timestamp time.Time
}

// ComplianceGuard enforces regulatory and tax-related trading constraints.
// Specifically: Pattern Day Trader (PDT) and Wash Sale rules.
type ComplianceGuard struct {
	AccountEquity    float64
	DayTrades        []DayTrade
	RealizedLosses   map[string]time.Time // Contract Signature -> Last Loss Timestamp
	WashSaleLosses   float64              // Total non-offsettable losses for Tax-Adjusted P/L
	mu               sync.Mutex
}

func NewComplianceGuard(initialEquity float64) *ComplianceGuard {
	return &ComplianceGuard{
		AccountEquity:  initialEquity,
		RealizedLosses: make(map[string]time.Time),
	}
}

// CheckPDTRule verifies FINRA Rule 4210 compliance.
// A pattern day trader (>= 4 day trades in 5 days) must maintain $25,000 equity.
func (g *ComplianceGuard) CheckPDTRule() error {
	g.mu.Lock()
	defer g.mu.Unlock()

	// 1. Clean up old trades outside the 5-day window
	cutoff := time.Now().AddDate(0, 0, -5)
	var validTrades []DayTrade
	for _, t := range g.DayTrades {
		if t.Timestamp.After(cutoff) {
			validTrades = append(validTrades, t)
		}
	}
	g.DayTrades = validTrades

	// 2. Enforce $25k limit if flagged or approaching flagging
	if len(g.DayTrades) >= 3 && g.AccountEquity < 25000.0 {
		return fmt.Errorf("PDT VIOLATION RISK: Account equity $%.2f < $25,000 threshold for pattern day trading", g.AccountEquity)
	}

	return nil
}

// CheckWashSaleRule ensures we don't realize a loss and immediately re-enter.
// This implementation blocks re-entry into a specific contract signature for 30 days if a loss was realized.
func (g *ComplianceGuard) CheckWashSaleRule(signature string) error {
	g.mu.Lock()
	defer g.mu.Unlock()

	lastLoss, exists := g.RealizedLosses[signature]
	if exists {
		if time.Since(lastLoss).Hours() < 24*30 {
			remaining := 30 - int(time.Since(lastLoss).Hours()/24)
			return fmt.Errorf("WASH SALE BLOCK: Realized loss on %s within last 30 days. Restricted for %d more days", signature, remaining)
		}
	}
	return nil
}

// RecordTradeOutcome updates the guard state after a trade is closed.
func (g *ComplianceGuard) RecordTradeOutcome(signature string, pnl float64, isDayTrade bool) {
	g.mu.Lock()
	defer g.mu.Unlock()

	if isDayTrade {
		g.DayTrades = append(g.DayTrades, DayTrade{Timestamp: time.Now()})
	}

	if pnl < 0 {
		g.RealizedLosses[signature] = time.Now()
		g.WashSaleLosses += -pnl
		fmt.Printf("COMPLIANCE: Logged realized loss of $%.2f on %s. Wash sale rule active for 30 days.\n", -pnl, signature)
	}
}

func (g *ComplianceGuard) UpdateEquity(equity float64) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.AccountEquity = equity
}
