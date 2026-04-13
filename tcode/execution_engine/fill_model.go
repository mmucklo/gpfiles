package main

// FillModel accounts for bid/ask spreads in SIMULATION mode.
// Random slippage has been removed: the fill price is fully deterministic
// given the same mid-price input, satisfying the no-fake-data mandate.
type FillModel struct {
	BaseSlippage float64 // retained for configuration but no longer randomised
}

func NewFillModel(baseSlippage float64) *FillModel {
	return &FillModel{BaseSlippage: baseSlippage}
}

// CalculateFillPrice returns the simulated fill price for a SIMULATION-mode order.
//
// In IBKR_PAPER / IBKR_LIVE modes this function is never called — fills come
// directly from IBKR.  For SIMULATION, we fill at the real chain mid-price ±
// a deterministic half-spread (1% of mid).  No random.* calls exist in this
// function; the same inputs always produce the same output.
//
// direction: "BUY" → fill at ask (mid + half-spread)
//            "SELL" → fill at bid (mid − half-spread)
func (f *FillModel) CalculateFillPrice(midPrice float64, direction string) float64 {
	spread := midPrice * 0.01 // 1% fixed spread
	if direction == "BUY" {
		return midPrice + (spread / 2.0)
	}
	return midPrice - (spread / 2.0)
}

// TripleConsensusSpot checks if the spot price is stable across multiple providers.
func (f *FillModel) TripleConsensusSpot(spotPrices []float64) (float64, bool) {
	if len(spotPrices) < 3 {
		return 0, false
	}
	sum := 0.0
	for _, p := range spotPrices {
		sum += p
	}
	avg := sum / float64(len(spotPrices))

	for _, p := range spotPrices {
		if (p-avg)/avg > 0.005 || (avg-p)/avg > 0.005 {
			return 0, false
		}
	}
	return avg, true
}
