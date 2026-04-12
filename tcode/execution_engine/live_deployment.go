package main

import (
	"fmt"
	"math/rand"
	"time"
)

// LiveCapitalGuard implements the Phase 4: Live Deployment Kill-Switch.
type LiveCapitalGuard struct {
	MaxDailyLoss    float64
	CurrentDailyPnL float64
	KillSwitch      bool
	AlertBot        *TelegramBot
}

func NewLiveCapitalGuard(maxLoss float64, bot *TelegramBot) *LiveCapitalGuard {
	return &LiveCapitalGuard{
		MaxDailyLoss: maxLoss,
		KillSwitch:   false,
		AlertBot:     bot,
	}
}

func (g *LiveCapitalGuard) CheckSafety(pnl float64) bool {
	if g.KillSwitch {
		return false
	}
	g.CurrentDailyPnL += pnl
	if g.CurrentDailyPnL <= -g.MaxDailyLoss {
		g.KillSwitch = true
		alertMsg := fmt.Sprintf("!!! KILL SWITCH TRIGGERED !!! Daily Loss Limit Reached: $%.2f. Trading HALTED.", g.CurrentDailyPnL)
		fmt.Println(alertMsg)
		g.AlertBot.SendAlert(alertMsg)
		return false
	}
	return true
}

func (g *LiveCapitalGuard) Reset() {
	g.CurrentDailyPnL = 0
	g.KillSwitch = false
	fmt.Println("COMPLIANCE: Live Capital Guard has been RESET. Trading resumed.")
}

// LiveDeployment orchestrates the final stage of the TSLA Alpha Engine.
func LiveDeployment(executor *IBKRExecutor, guard *LiveCapitalGuard, pricing *PricingEngine) {
	fmt.Println("\nPHASE 4: Live Deployment (Live Capital) Activated.")
	
	// Simulation of live trading with high-stakes pnl swings
	for i := 0; i < 10; i++ {
		if guard.KillSwitch {
			fmt.Println("Trading Halted: Awaiting Human Intervention via Telegram.")
			break
		}

		// Simulated PnL swing
		swing := (rand.Float64() - 0.7) * 5000.0 // Skewed towards loss for test
		if guard.CheckSafety(swing) {
			fmt.Printf("Live Trade Execution Successful. Current PnL: $%.2f\n", guard.CurrentDailyPnL)
		}
		
		time.Sleep(10 * time.Millisecond)
	}
}
