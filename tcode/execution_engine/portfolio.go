package main

import (
	"fmt"
	"sync"
	"time"
)

type Position struct {
	Ticker        string    `json:"ticker"`
	OptionType    string    `json:"option_type"`
	Strike        float64   `json:"strike"`
	Expiry        string    `json:"expiry"`
	EntryPrice    float64   `json:"entry_price"`
	CurrentPrice  float64   `json:"current_price"`
	Quantity      int       `json:"quantity"`
	UnrealizedPnL float64   `json:"unrealized_pnl"`
	EntryTime     time.Time `json:"entry_time"`
}

type PaperPortfolio struct {
	NAV               float64             `json:"nav"`
	Cash              float64             `json:"cash"`
	RealizedPnL       float64             `json:"realized_pnl"`
	MaintenanceMargin float64             `json:"maintenance_margin"`
	Positions         map[string]Position `json:"positions"`
	mu                sync.RWMutex
}

func NewPaperPortfolio(initialCash float64) *PaperPortfolio {
	return &PaperPortfolio{
		NAV:               initialCash,
		Cash:              initialCash,
		RealizedPnL:       0,
		MaintenanceMargin: 0,
		Positions:         make(map[string]Position),
	}
}

func (p *PaperPortfolio) GetContractSignature(ticker, optType, expiry string, strike float64) string {
	return fmt.Sprintf("%s_%s_%s_%.2f", ticker, optType, expiry, strike)
}

func (p *PaperPortfolio) UpdatePositions(spot float64, pricing *PricingEngine) {
	p.mu.Lock()
	defer p.mu.Unlock()

	// Try to get real chain prices
	chainPrices := fetchChainPrices()

	var totalMarketValue float64
	for sig, pos := range p.Positions {
		var newPremium float64

		// Look up real price from chain cache
		chainKey := fmt.Sprintf("%s_%.2f_%s", pos.OptionType, pos.Strike, pos.Expiry)
		if realPrice, ok := chainPrices[chainKey]; ok && realPrice > 0 {
			newPremium = realPrice
		} else {
			// Fallback: use entry price (flat — no fantasy gains)
			newPremium = pos.EntryPrice
		}

		pos.CurrentPrice = newPremium
		pos.UnrealizedPnL = (newPremium - pos.EntryPrice) * float64(pos.Quantity) * 100
		p.Positions[sig] = pos
		totalMarketValue += newPremium * float64(pos.Quantity) * 100
	}

	p.MaintenanceMargin = totalMarketValue
	p.NAV = p.Cash + totalMarketValue
}

func (p *PaperPortfolio) OpenPosition(pos Position) error {
	p.mu.Lock()
	defer p.mu.Unlock()

	cost := float64(pos.Quantity) * pos.EntryPrice * 100
	if cost > p.Cash {
		return fmt.Errorf("insufficient cash")
	}

	sig := p.GetContractSignature(pos.Ticker, pos.OptionType, pos.Expiry, pos.Strike)
	pos.CurrentPrice = pos.EntryPrice
	if existing, ok := p.Positions[sig]; ok {
		totalQty := existing.Quantity + pos.Quantity
		totalCost := (existing.EntryPrice * float64(existing.Quantity)) + (pos.EntryPrice * float64(pos.Quantity))
		existing.EntryPrice = totalCost / float64(totalQty)
		existing.Quantity = totalQty
		existing.CurrentPrice = pos.EntryPrice
		p.Positions[sig] = existing
	} else {
		p.Positions[sig] = pos
	}

	p.Cash -= cost
	return nil
}

func (p *PaperPortfolio) ClosePosition(ticker, optType, expiry string, strike float64, exitPrice float64, quantity int) (float64, error) {
	p.mu.Lock()
	defer p.mu.Unlock()

	sig := p.GetContractSignature(ticker, optType, expiry, strike)
	pos, ok := p.Positions[sig]
	if !ok {
		return 0, fmt.Errorf("position not found")
	}

	if quantity > pos.Quantity {
		return 0, fmt.Errorf("insufficient quantity")
	}

	realizedPnL := (exitPrice - pos.EntryPrice) * float64(quantity) * 100
	p.Cash += float64(quantity) * exitPrice * 100
	p.RealizedPnL += realizedPnL
	
	if quantity == pos.Quantity {
		delete(p.Positions, sig)
	} else {
		pos.Quantity -= quantity
		p.Positions[sig] = pos
	}

	return realizedPnL, nil
}

func (p *PaperPortfolio) GetTradableEquity() float64 {
	p.mu.RLock()
	defer p.mu.RUnlock()
	return p.Cash
}
