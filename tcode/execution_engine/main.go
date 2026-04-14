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
	"net/http"
	"os"
	"strconv"
	"strings"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
)

func envOrDefault(k, d string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return d
}

func envIntOrDefault(k string, d int) int {
	if v := os.Getenv(k); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			return n
		}
	}
	return d
}

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



func main() {
	// Setup logging to file
	logFile, err := os.OpenFile("executor.log", os.O_CREATE|os.O_WRONLY|os.O_APPEND, 0666)
	if err != nil {
		log.Fatalf("Failed to open log file: %v", err)
	}
	log.SetOutput(logFile)

	// Resolve execution mode from EXECUTION_MODE env var (default: IBKR_PAPER).
	initExecutionMode()
	log.Printf("Execution mode: %s", ActiveExecutionMode)

	pe := &PricingEngine{}
	
	// Setup Compliance Guard (Task C: PDT & Wash Sale)
	compliance := NewComplianceGuard(1000000.0)
	executor := NewIBKRExecutor(1000000.0, compliance)
	log.Printf("Portfolio initialized: NAV=$%.2f Cash=$%.2f Positions=%d", executor.Portfolio.NAV, executor.Portfolio.Cash, len(executor.Portfolio.Positions))

	// Setup External Alert Connectivity
	bot := NewTelegramBot(os.Getenv("TELEGRAM_TOKEN"), os.Getenv("TELEGRAM_CHAT_ID"))
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
	mux.HandleFunc("/api/metrics/publisher", configHandler.ServePublisherMetrics)
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
	mux.HandleFunc("/api/orders/pending", configHandler.ServeOrdersPending)
	mux.HandleFunc("/api/orders/cancel", configHandler.ServeOrdersCancel)
	mux.HandleFunc("/api/orders/cap-events", configHandler.ServeCapEvents)
	mux.HandleFunc("/api/positions/close", configHandler.ServePositionsClose)
	mux.HandleFunc("/api/config/notional", configHandler.ServeNotionalConfig)

	// Signal feedback & cancel (Phase 13)
	mux.HandleFunc("/api/signals/feedback/recent", configHandler.ServeSignalFeedbackRecent)
	mux.HandleFunc("/api/signals/feedback/digest", configHandler.ServeSignalFeedbackDigest)
	mux.HandleFunc("/api/signals/feedback/resolve", configHandler.ServeSignalFeedbackResolve)
	mux.HandleFunc("/api/signals/feedback", configHandler.ServeSignalFeedback)
	mux.HandleFunc("/api/signals/cancel", configHandler.ServeSignalCancel)

	// System heartbeats (Phase 13.6)
	mux.HandleFunc("/api/system/heartbeats", configHandler.ServeSystemHeartbeats)
	mux.HandleFunc("/api/system/alerts", configHandler.ServeSystemAlerts)
	// Prefix-match for /{component}/sparkline and /{component}/restart
	mux.HandleFunc("/api/system/heartbeats/", func(w http.ResponseWriter, r *http.Request) {
		path := r.URL.Path
		if strings.HasSuffix(path, "/sparkline") {
			configHandler.ServeSystemHeartbeatSparkline(w, r)
		} else if strings.HasSuffix(path, "/restart") {
			configHandler.ServeSystemHeartbeatRestart(w, r)
		} else {
			http.NotFound(w, r)
		}
	})

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

	// Order path: SIMULATION uses internal PaperPortfolio.
	// IBKR_PAPER/IBKR_LIVE uses Python subprocess (ingestion/ibkr_order.py) per order.
	// No persistent TCP connection is held by the Go engine — each subprocess
	// opens and closes its own ib_insync connection using a unique client ID.
	if ActiveExecutionMode == ModeSimulation {
		log.Printf("SIMULATION mode: no broker subprocess — /api/account returns error.")
	} else {
		log.Printf("IBKR order path: subprocess via ingestion/ibkr_order.py "+
			"(host=%s port=%s mode=%s)",
			envOrDefault("IBKR_HOST", "127.0.0.1"),
			envOrDefault("IBKR_PORT", "4002"),
			ActiveExecutionMode)
	}

	// ── Phase 9: startup global cancel ──────────────────────────────────────
	// Clear any orphan pre-bracket naked orders before the first bracket is placed.
	// Gated: STARTUP_CLEAR_ORPHANS=1 (default) and non-SIMULATION mode.
	if ActiveExecutionMode != ModeSimulation && envIntOrDefault("STARTUP_CLEAR_ORPHANS", 1) == 1 {
		log.Println("[STARTUP] Global cancel issued to clear pre-bracket naked orders")
		count, gcErr := StartupGlobalCancel()
		if gcErr != nil {
			log.Printf("[STARTUP] Global cancel failed (non-fatal): %v", gcErr)
		} else {
			log.Printf("[STARTUP] %d open orders after global cancel", count)
		}
	}

	subscriber.Start()
	defer subscriber.Close()

	// Phase 13: keep cancelled-signal cache current (10s refresh from DB)
	StartCancelRefreshLoop()

	// Phase 13.6: IBKR status heartbeat — 60s ticker, polls open orders as liveness probe.
	go func() {
		ticker := time.NewTicker(60 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			_, err := OpenIBKROrders()
			if err != nil {
				emitEngineHeartbeat("engine_ibkr_status", "degraded", err.Error())
			} else {
				emitEngineHeartbeat("engine_ibkr_status", "ok", "")
			}
		}
	}()

	// Periodic Portfolio Revaluation (Task: Real-time NAV & Unrealized PnL)
	go func() {
		for {
			executor.Portfolio.UpdatePositions()
			time.Sleep(5 * time.Second)
		}
	}()

	// ── Phase 9: expiry-close scheduler ──────────────────────────────────────
	// Scans open positions every 60s between 15:25–15:35 ET on weekdays.
	// Places SELL MARKET TIF=DAY for any position expiring today.
	go func() {
		loc, locErr := time.LoadLocation("America/New_York")
		if locErr != nil {
			log.Printf("[EXPIRY-CLOSE] Could not load America/New_York timezone: %v", locErr)
			return
		}
		for {
			time.Sleep(60 * time.Second)
			if ActiveExecutionMode == ModeSimulation {
				continue
			}
			etNow := time.Now().In(loc)
			wd    := etNow.Weekday()
			if wd == time.Saturday || wd == time.Sunday {
				continue
			}
			etMin := etNow.Hour()*60 + etNow.Minute()
			if etMin >= 15*60+25 && etMin <= 15*60+35 {
				today := etNow.Format("2006-01-02")
				log.Printf("[EXPIRY-CLOSE] Window open (ET %02d:%02d) — checking positions expiring %s",
					etNow.Hour(), etNow.Minute(), today)
				ExpiryCloseIBKROrders(today)
			}
		}
	}()

	// Keep-alive for the event-driven subscriber
	log.Println("System Heartbeat: Online and Awaiting Signals.")

	log.Println("Signal-driven execution active — awaiting NATS signals.")
	// Blocking wait to prevent the engine from exiting prematurely.
	select {}
}
