package main

import (
	"encoding/json"
	"fmt"
	"log"
	"time"

	"github.com/nats-io/nats.go"
)

// AlphaSignal represents the incoming conviction signal from the Python Alpha Engine.
type AlphaSignal struct {
	ModelID           string  `json:"model_id"`
	Direction         string  `json:"direction"`
	Confidence        float64 `json:"confidence"`
	Timestamp         float64 `json:"timestamp"`
	Ticker            string  `json:"ticker"`
	UnderlyingPrice   float64 `json:"underlying_price"`
	PriceSource       string  `json:"price_source"`
	StrategyCode      string  `json:"strategy_code"`
	RecommendedStrike float64 `json:"recommended_strike"`
	ShortStrike       float64 `json:"short_strike"`
	LongStrike        float64 `json:"long_strike"`
	IsSpread          bool    `json:"is_spread"`
	RecommendedExpiry string  `json:"recommended_expiry"`
	OptionType        string  `json:"option_type"`
	Action            string  `json:"action"`
	ExpirationDate    string  `json:"expiration_date"`
	TargetLimitPrice  float64 `json:"target_limit_price"`
	TakeProfitPrice   float64 `json:"take_profit_price"`
	StopLossPrice     float64 `json:"stop_loss_price"`
	KellyWagerPct     float64 `json:"kelly_wager_pct"`
	Quantity          int     `json:"quantity"`
	ConfidenceRationale string                 `json:"confidence_rationale"`
	ImpliedVolatility   float64                `json:"implied_volatility"`
	SpotSources         map[string]interface{} `json:"spot_sources,omitempty"`
}

// SignalSubscriber listens for Alpha Engine broadcasts and triggers execution.
type SignalSubscriber struct {
	Conn       *nats.Conn
	Executor   *IBKRExecutor
	Pricing    *PricingEngine
	Guard      *LiveCapitalGuard
	Compliance *ComplianceGuard
	Archive    *ArchiveSink
}

func NewSignalSubscriber(natsURL string, executor *IBKRExecutor, pricing *PricingEngine, guard *LiveCapitalGuard, compliance *ComplianceGuard, archive *ArchiveSink) *SignalSubscriber {
	nc, err := nats.Connect(natsURL)
	if err != nil {
		log.Printf("NATS Connection Error: %v (Proceeding with offline mode for test)", err)
		return &SignalSubscriber{Executor: executor, Pricing: pricing, Guard: guard, Compliance: compliance, Archive: archive}
	}
	return &SignalSubscriber{Conn: nc, Executor: executor, Pricing: pricing, Guard: guard, Compliance: compliance, Archive: archive}
}

func (s *SignalSubscriber) Start() {
	if s.Conn == nil {
		fmt.Println("Signal Subscriber: Offline Mode. No NATS server found.")
		return
	}

	fmt.Println("Signal Subscriber: Listening for 'tsla.alpha.signals'...")

	// Async subscription to ensure low-latency signal consumption.
	_, err := s.Conn.Subscribe("tsla.alpha.signals", func(m *nats.Msg) {
		fmt.Printf("DEBUG: Received raw message on 'tsla.alpha.signals': %s\n", string(m.Data))
		start := time.Now()
		var sig AlphaSignal
		if err := json.Unmarshal(m.Data, &sig); err != nil {
			log.Printf("Signal Decoding Error: %v", err)
			return
		}

		fmt.Printf("Consensus Signal Received: %s [%.2f] @ %.0f\n", sig.Direction, sig.Confidence, sig.Timestamp)
		
		// Capture for UI Dashboard
		AddSignal(sig)

		// Task 2: Update Prometheus Metrics
		SignalAgreement.Set(sig.Confidence)
		
		// Task 3: Record to Archive Sink
		if s.Archive != nil {
			s.Archive.RecordSignal(sig.ModelID, sig.Direction, sig.Confidence)
		}

		// Map signal to Execution logic
		if sig.Direction == "BULLISH" && sig.Confidence > 0.8 {
			if s.Guard.KillSwitch {
				fmt.Println("SIGNAL BLOCKED: Live Capital Guard Kill-Switch is active.")
				return
			}

			ticker := sig.Ticker
			if ticker == "" {
				ticker = "TSLA"
			}
			strike := sig.RecommendedStrike
			if sig.IsSpread {
				strike = sig.ShortStrike
			}
			
			if err := s.Compliance.CheckPDTRule(); err != nil {
				fmt.Printf("SIGNAL BLOCKED: %v\n", err)
				return
			}
			
			signature := s.Executor.Portfolio.GetContractSignature(ticker, sig.OptionType, sig.ExpirationDate, strike)
			if err := s.Compliance.CheckWashSaleRule(signature); err != nil {
				fmt.Printf("SIGNAL BLOCKED: %v\n", err)
				return
			}

			price := sig.TargetLimitPrice
			if price <= 0 {
				price = s.Pricing.CallPrice(sig.UnderlyingPrice, strike, 0.08, 0.04, 0.50)
			}
			
			qty := sig.Quantity
			if qty <= 0 {
				qty = 1 // Default to 1 for test
			}
			
			if sig.Action == "SELL" {
				qty = -qty
			}

			s.Executor.ExecuteOrder(ticker, sig.OptionType, strike, sig.ExpirationDate, qty, price)
			
			// Task 2: Record Latency
			ExecutionLatency.Observe(time.Since(start).Seconds())
			
			// Task 3: Record Trade to Archive
			if s.Archive != nil {
				s.Archive.RecordTrade(ticker, "BUY", 10, price, 0.0)
			}
		}
	})

	_, err = s.Conn.Subscribe("tsla.alpha.sim", func(m *nats.Msg) {
		UpdateSimState(m.Data)
	})

	if err != nil {
		log.Fatalf("Subscription Error: %v", err)
	}
}

func (s *SignalSubscriber) Close() {
	if s.Conn != nil {
		s.Conn.Close()
	}
}
