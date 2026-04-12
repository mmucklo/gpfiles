package main

import (
	"math/rand"
)

// FillModel accounts for bid/ask spreads and slippage.
type FillModel struct {
	BaseSlippage float64 // e.g., 0.01 for 1%
}

func NewFillModel(baseSlippage float64) *FillModel {
	return &FillModel{BaseSlippage: baseSlippage}
}

// CalculateFillPrice calculates the executed price given a mid price.
// direction: "BUY" or "SELL"
func (f *FillModel) CalculateFillPrice(midPrice float64, direction string) float64 {
	// Assume 1% spread + random slippage up to BaseSlippage
	spread := midPrice * 0.01
	slippage := midPrice * (rand.Float64() * f.BaseSlippage)
	
	if direction == "BUY" {
		// Buy at Ask (Mid + Spread/2 + Slippage)
		return midPrice + (spread / 2.0) + slippage
	} else {
		// Sell at Bid (Mid - Spread/2 - Slippage)
		return midPrice - (spread / 2.0) - slippage
	}
}

// TripleConsensusSpot checks if the spot price is stable across multiple providers.
// (In a real system, this would query NATS or multiple APIs).
func (f *FillModel) TripleConsensusSpot(spotPrices []float64) (float64, bool) {
	if len(spotPrices) < 3 {
		return 0, false
	}
	// Check for outliers (max 0.5% deviation)
	sum := 0.0
	for _, p := range spotPrices {
		sum += p
	}
	avg := sum / float64(len(spotPrices))
	
	for _, p := range spotPrices {
		if (p-avg)/avg > 0.005 || (avg-p)/avg > 0.005 {
			return 0, false // Divergence detected
		}
	}
	return avg, true
}
