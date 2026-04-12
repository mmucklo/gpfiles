package main

/*
#cgo LDFLAGS: -lm
#include <math.h>

// Black-Scholes Formula: C = S*N(d1) - K*e^(-rt)*N(d2)
double normal_cdf(double x) {
    return 0.5 * erfc(-x * M_SQRT1_2);
}

double calculate_call_price(double S, double K, double T, double r, double sigma) {
    if (T <= 0) return (S > K) ? (S - K) : 0;
    double d1 = (log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * sqrt(T));
    double d2 = d1 - sigma * sqrt(T);
    return S * normal_cdf(d1) - K * exp(-r * T) * normal_cdf(d2);
}
*/
import "C"
import (
	"log"
	"math/rand"
	"net/http"
	"os"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// PricingEngine wraps the C-based Black-Scholes implementation.
type PricingEngine struct{}

func (p *PricingEngine) CallPrice(S, K, T, r, sigma float64) float64 {
	return float64(C.calculate_call_price(C.double(S), C.double(K), C.double(T), C.double(r), C.double(sigma)))
}

// IBKRExecutor handles the Paper Trading simulation via the IBKR API logic.
type IBKRExecutor struct {
	AccountBalance float64
	OpenPositions  map[string]int
	Portfolio      *PaperPortfolio
	FillModel      *FillModel
	IsPaperTrading bool
	Compliance     *ComplianceGuard
}

func NewIBKRExecutor(initialBalance float64, compliance *ComplianceGuard) *IBKRExecutor {
	portfolio := NewPaperPortfolio(initialBalance)
	return &IBKRExecutor{
		AccountBalance: initialBalance,
		OpenPositions:  make(map[string]int),
		Portfolio:      portfolio,
		FillModel:      NewFillModel(0.005), // 0.5% base slippage
		IsPaperTrading: true,               // Default to paper trading
		Compliance:     compliance,
	}
}

func (e *IBKRExecutor) ExecuteOrder(ticker string, optType string, strike float64, expiry string, quantity int, midPrice float64) {
	direction := "BUY"
	if quantity < 0 {
		direction = "SELL"
	}
	
	price := e.FillModel.CalculateFillPrice(midPrice, direction)
	
	signature := e.Portfolio.GetContractSignature(ticker, optType, expiry, strike)
	
	if direction == "BUY" {
		// Wash Sale Check
		if err := e.Compliance.CheckWashSaleRule(signature); err != nil {
			log.Printf("IBKR ERROR: %v", err)
			return
		}

		// Position concentration check: max 50 contracts per symbol
		if existing, ok := e.Portfolio.Positions[signature]; ok {
			if existing.Quantity >= 50 {
				log.Printf("POSITION LIMIT: Already hold %d contracts of %s, skipping", existing.Quantity, signature)
				return
			}
		}

		// Exposure check: max 5% of NAV per trade
		totalExposure := float64(quantity) * price * 100
		if totalExposure > e.Portfolio.NAV*0.05 {
			log.Printf("EXPOSURE LIMIT: $%.0f exceeds 5%% of NAV $%.0f, reducing", totalExposure, e.Portfolio.NAV)
			quantity = int(e.Portfolio.NAV * 0.05 / (price * 100))
			if quantity < 1 {
				log.Printf("EXPOSURE LIMIT: Position too large, skipping")
				return
			}
		}

		pos := Position{
			Ticker:     ticker,
			OptionType: optType,
			Strike:     strike,
			Expiry:     expiry,
			EntryPrice: price,
			Quantity:   quantity,
			EntryTime:  time.Now(),
		}
		actualCost := float64(quantity) * price * 100
		if err := e.Portfolio.OpenPosition(pos); err != nil {
			log.Printf("IBKR ERROR: %v", err)
			return
		}
		log.Printf("IBKR SUCCESS: Purchased %d contracts of %s at $%.2f (Cash: $%.2f)", quantity, signature, price, e.Portfolio.Cash)
		AddTradeLog(TradeLog{Time: time.Now(), Ticker: signature, Action: "BUY", Quantity: quantity, Price: price, Cost: actualCost})
	} else {
		actualCredit := float64(-quantity) * price * 100
		pnl, err := e.Portfolio.ClosePosition(ticker, optType, expiry, strike, price, -quantity)
		if err != nil {
			log.Printf("IBKR ERROR: %v", err)
			return
		}
		e.Compliance.RecordTradeOutcome(signature, pnl, true) // Assume day trade for now
		log.Printf("IBKR SUCCESS: Sold %d contracts of %s at $%.2f (PnL: $%.2f, Cash: $%.2f)", -quantity, signature, price, pnl, e.Portfolio.Cash)
		
		netProfit := pnl
		if pnl < 0 {
			netProfit = 0 // Wash sale loss not deductible for 'Net Profit' HUD
		}
		AddTradeLog(TradeLog{Time: time.Now(), Ticker: signature, Action: "SELL", Quantity: -quantity, Price: price, Cost: actualCredit, PnL: pnl, NetProfit: netProfit})
	}
	
	e.Compliance.UpdateEquity(e.Portfolio.NAV)
	TradeCount.Inc()
}

func PaperTradingLoop(executor *IBKRExecutor, pricing *PricingEngine) {
	log.Println("PHASE 3: Paper Trading (IBKR) Simulation Active.")
	for i := 0; i < 3; i++ {
		conf := 0.75 + rand.Float64()*0.2
		if conf > 0.8 {
			ticker := "TSLA"
			expiry := "2026-03-20"
			strike := 210.0
			price := pricing.CallPrice(200, strike, 0.08, 0.04, 0.50)
			executor.ExecuteOrder(ticker, "CALL", strike, expiry, 1, price)
			
			time.Sleep(100 * time.Millisecond)
			// Simulate immediate exit for testing PnL/Wash Sale
			exitPrice := price * 1.05
			executor.ExecuteOrder(ticker, "CALL", strike, expiry, -1, exitPrice)
		}
		time.Sleep(10 * time.Millisecond)
	}
}

// fetchConsensusPrice is a mock bridge to the MultiSourcePricing logic.
// In a real setup, this would be a CGO call or a separate microservice.
func fetchConsensusPrice() (float64, error) {
	// For this build, we simulate the consensus price around the high $300s
	return 392.78 + (rand.Float64() * 2.0) - 1.0, nil
}

func main() {
	// Setup logging to file
	logFile, err := os.OpenFile("executor.log", os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0666)
	if err != nil {
		log.Fatalf("Failed to open log file: %v", err)
	}
	log.SetOutput(logFile)

	rand.Seed(time.Now().UnixNano())
	pe := &PricingEngine{}
	
	// Setup Compliance Guard (Task C: PDT & Wash Sale)
	compliance := NewComplianceGuard(1000000.0)
	executor := NewIBKRExecutor(1000000.0, compliance)
	log.Printf("Portfolio initialized: NAV=$%.2f Cash=$%.2f Positions=%d", executor.Portfolio.NAV, executor.Portfolio.Cash, len(executor.Portfolio.Positions))

	// Setup Credential Management & Real Handshake (Task 3)
	creds := &CredentialManager{}
	os.Setenv("IBKR_USERNAME", "alpha_trader") // Mocking SOPS injection
	os.Setenv("IBKR_PASSWORD", "bulletproof_pass")

	// Setup External Alert Connectivity
	bot := NewTelegramBot(creds.GetSecret("TELEGRAM_TOKEN"), creds.GetSecret("TELEGRAM_CHAT_ID"))
	guard := NewLiveCapitalGuard(10000.0, bot) // $10k Daily Loss Limit

	// Setup Archive Sink (PostgreSQL)
	dbURL := os.Getenv("DB_URL")
	if dbURL == "" {
		dbURL = "postgres://alpha_admin:alpha_pass@localhost:5432/alpha_db?sslmode=disable"
	}
	archive, _ := NewArchiveSink(dbURL)

	// Setup the Central Nervous System (NATS)
	subscriber := NewSignalSubscriber("nats://127.0.0.1:4222", executor, pe, guard, compliance, archive)

	// Setup Control Center API
	configHandler := &ConfigHandler{Executor: executor, Guard: guard, Portfolio: executor.Portfolio, NatsConn: subscriber.Conn}
	mux := http.NewServeMux()
	mux.Handle("/api/config", configHandler)
	mux.HandleFunc("/api/signals", configHandler.ServeSignals)
	mux.HandleFunc("/api/signals/all", configHandler.ServeAllSignals)
	mux.HandleFunc("/api/simulation", configHandler.ServeSimulation)
	mux.HandleFunc("/api/simulation/toggle", configHandler.ToggleSimulation)
	mux.HandleFunc("/api/guard/reset", configHandler.ResetGuard)
	mux.HandleFunc("/api/portfolio", configHandler.ServePortfolio)
	mux.HandleFunc("/api/trades", configHandler.ServeTrades)
	mux.HandleFunc("/api/metrics/requests", configHandler.ServeRequestMetrics)
	mux.HandleFunc("/api/metrics/signals", configHandler.ServeSignalMetrics)
	mux.HandleFunc("/api/logs", configHandler.ServeLogs)
	mux.HandleFunc("/api/metrics/vitals", configHandler.ServeVitals)
	mux.HandleFunc("/api/metrics/latency", configHandler.ServeLatencyMetrics)
	mux.HandleFunc("/api/metrics/signals/breakdown", configHandler.ServeSignalBreakdown)
	mux.HandleFunc("/api/metrics/nats", configHandler.ServeNatsHealth)
	mux.HandleFunc("/api/metrics/buildinfo", configHandler.ServeBuildInfo)
	mux.HandleFunc("/api/metrics/goroutines", configHandler.ServeGoroutineProfile)
	mux.HandleFunc("/api/broker/status", configHandler.ServeBrokerStatus)
	mux.HandleFunc("/api/closed_trades", configHandler.ServeClosedTrades)
	mux.HandleFunc("/api/fills/detail", configHandler.ServeFillDetail)
	mux.HandleFunc("/api/status", configHandler.ServeStatus)
	mux.HandleFunc("/api/gastown/status", configHandler.ServeGastownStatus)
	mux.HandleFunc("/api/gastown/log", configHandler.ServeGastownLog)
	mux.HandleFunc("/api/gastown/history", configHandler.ServeGastownHistory)
	mux.HandleFunc("/api/system/state", configHandler.ServeSystemState)
	mux.HandleFunc("/api/data/audit", configHandler.ServeDataAudit)
	mux.HandleFunc("/api/account", configHandler.ServeAccount)
	mux.HandleFunc("/api/positions", configHandler.ServePositions)
	mux.HandleFunc("/api/fills", configHandler.ServeFills)
	mux.HandleFunc("/api/sim/reset", configHandler.ServeSimReset)
	mux.HandleFunc("/api/sim/toggle", configHandler.ServeSimToggle)
	mux.HandleFunc("/api/intel", configHandler.ServeIntel)
	mux.HandleFunc("/api/options/chain", configHandler.ServeOptionsChain)
	mux.HandleFunc("/api/losing_trades", configHandler.ServeLosingTrades)
	mux.HandleFunc("/api/scorecard", configHandler.ServeScorecard)
	mux.HandleFunc("/api/losses", configHandler.ServeLossSummary)
	mux.HandleFunc("/api/fills/tag", configHandler.ServeTagTrade)

	// Live Reload WebSocket (Task: Auto-Refresh)
	mux.Handle("/dev/ws", GlobalReloader)
	mux.HandleFunc("/dev/reload", TriggerReloadHandler)

	// Serve Production UI Assets (Task: Unified Port Stability)
	// SPA fallback: serve index.html for unknown paths (React Router client-side routing)
	uiPath := "./alpha_control_center/dist"
	fileServer := http.FileServer(http.Dir(uiPath))
	mux.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) {
		path := uiPath + r.URL.Path
		if _, err := os.Stat(path); os.IsNotExist(err) {
			http.ServeFile(w, r, uiPath+"/index.html")
			return
		}
		fileServer.ServeHTTP(w, r)
	})

	// Setup Observability (Prometheus)
	go func() {
		// Unified port 2112 for UI, API, and Metrics
		mux.Handle("/metrics", promhttp.Handler())
		log.Println("Alpha Control Center UI, API, and Metrics available at :2112")
		if err := http.ListenAndServe(":2112", RateLimiterMiddleware(RequestLoggingMiddleware(mux))); err != nil {
			log.Fatalf("Server Fatal: %v", err)
		}
	}()

	ibClient := NewIBKRClient("127.0.0.1", 7497, 1)
	if err := ibClient.Connect(); err != nil {
		log.Printf("Handshake Warning: %v (Simulated offline)", err)
	}

	subscriber.Start()
	defer subscriber.Close()

	// Periodic Portfolio Revaluation (Task: Real-time NAV & Unrealized PnL)
	go func() {
		for {
			spot, err := fetchConsensusPrice() 
			if err == nil {
				executor.Portfolio.UpdatePositions(spot, pe)
			}
			time.Sleep(5 * time.Second)
		}
	}()

	// Keep-alive for the event-driven subscriber
	log.Println("System Heartbeat: Online and Awaiting Signals.")

	log.Println("Signal-driven execution active — awaiting NATS signals.")
	// Blocking wait to prevent the engine from exiting prematurely.
	select {}
}
