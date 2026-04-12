package main

import (
	"github.com/prometheus/client_golang/prometheus"
	"github.com/prometheus/client_golang/prometheus/promauto"
)

var (
	// Metric: Execution Latency (Microseconds)
	ExecutionLatency = promauto.NewHistogram(prometheus.HistogramOpts{
		Name: "alpha_execution_latency_seconds",
		Help: "The latency of the Go execution engine order placement.",
		Buckets: prometheus.DefBuckets,
	})

	// Metric: Signal Agreement Score
	SignalAgreement = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "alpha_signal_agreement_score",
		Help: "The current consensus agreement score from the Alpha Engine.",
	})

	// Metric: Real-time PnL
	PortfolioPnL = promauto.NewGauge(prometheus.GaugeOpts{
		Name: "alpha_portfolio_pnl",
		Help: "The total intraday PnL of the Alpha Engine.",
	})

	// Metric: Trade Count
	TradeCount = promauto.NewCounter(prometheus.CounterOpts{
		Name: "alpha_trade_total",
		Help: "The total number of executed trades.",
	})
)
