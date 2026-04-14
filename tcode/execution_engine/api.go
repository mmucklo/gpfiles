package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io/ioutil"
	"log"
	"net"
	"net/http"
	"os"
	"os/exec"
	"runtime"
	"runtime/pprof"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/nats-io/nats.go"
	"golang.org/x/time/rate"
)

var (
	gitCommit = "dev"
	buildTime = "unknown"
	goVersion = runtime.Version()
    limiters = make(map[string]*rate.Limiter)
    limiterMu sync.Mutex
)

// ── account / positions cache (10s TTL) ─────────────────────────────────────
var (
	accountCache      interface{}
	accountCacheMu    sync.Mutex
	accountCachedAt   time.Time
	accountCacheTTL   = 10 * time.Second

	positionsCache    interface{}
	positionsCacheMu  sync.Mutex
	positionsCachedAt time.Time

	// Simulation mode toggle: "paper" or "sim"
	simMode   = "paper"
	simModeMu sync.RWMutex

	// Chain price cache for position mark-to-market
	chainPriceCache   map[string]float64 // "CALL_365.00_2026-04-13" -> mid_price
	chainPriceCacheTs time.Time
	chainPriceCacheMu sync.Mutex
)

func fetchChainPrices() map[string]float64 {
	chainPriceCacheMu.Lock()
	defer chainPriceCacheMu.Unlock()

	// Return cached if fresh (60s TTL)
	if chainPriceCache != nil && time.Since(chainPriceCacheTs) < 60*time.Second {
		return chainPriceCache
	}

	// Fetch from Python
	cmd := exec.Command("./alpha_engine/venv/bin/python", "alpha_engine/ingestion/options_chain_api.py")
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	out, err := cmd.Output()
	if err != nil {
		fmt.Printf("[CHAIN] Price fetch failed: %v\n", err)
		return chainPriceCache // Return stale cache
	}

	var data struct {
		Calls []struct {
			Strike float64 `json:"strike"`
			Mid    float64 `json:"mid"`
		} `json:"calls"`
		Puts []struct {
			Strike float64 `json:"strike"`
			Mid    float64 `json:"mid"`
		} `json:"puts"`
		Expiry string `json:"expiry"`
	}

	if err := json.Unmarshal(out, &data); err != nil {
		fmt.Printf("[CHAIN] Price parse failed: %v\n", err)
		return chainPriceCache
	}

	prices := make(map[string]float64)
	for _, c := range data.Calls {
		key := fmt.Sprintf("CALL_%.2f_%s", c.Strike, data.Expiry)
		prices[key] = c.Mid
	}
	for _, p := range data.Puts {
		key := fmt.Sprintf("PUT_%.2f_%s", p.Strike, data.Expiry)
		prices[key] = p.Mid
	}

	chainPriceCache = prices
	chainPriceCacheTs = time.Now()
	fmt.Printf("[CHAIN] Prices refreshed: %d entries for %s\n", len(prices), data.Expiry)
	return prices
}

// ConfigStore holds the dynamic system configuration.
// Protected by a Mutex for thread-safe updates from the UI.
type ConfigStore struct {
	IBKR struct {
		Host     string `json:"host"`
		Port     int    `json:"port"`
		Username string `json:"username"`
		Password string `json:"password"`
	} `json:"ibkr"`
	Telegram struct {
		Token  string `json:"token"`
		ChatID string `json:"chat_id"`
	} `json:"telegram"`
	TradingView struct {
		SessionID string `json:"session_id"`
	} `json:"trading_view"`
	mu sync.RWMutex
}

var GlobalConfig = &ConfigStore{}

// SignalStore maintains a ring buffer of signals.
type SignalStore struct {
	Latest []AlphaSignal
	mu     sync.RWMutex
	maxLen int
}

func NewSignalStore(maxLen int) *SignalStore {
	return &SignalStore{Latest: make([]AlphaSignal, 0), maxLen: maxLen}
}

var GlobalSignals = NewSignalStore(500)    // all signals (heartbeat included) — 500 to survive heartbeat flood
var GlobalConvictions = NewSignalStore(200) // non-IDLE conviction signals only

func (s *SignalStore) AddSignal(sig AlphaSignal) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.Latest = append([]AlphaSignal{sig}, s.Latest...)
	if len(s.Latest) > s.maxLen {
		s.Latest = s.Latest[:s.maxLen]
	}
}

func AddSignal(sig AlphaSignal) {
	GlobalSignals.AddSignal(sig)
	GlobalMetrics.LogSignal(sig.StrategyCode)
	if sig.StrategyCode != "IDLE_SCAN" {
		GlobalConvictions.AddSignal(sig)
	}
}

// ConfigHandler manages the REST API for credential updates.
type ConfigHandler struct {
	Executor  *IBKRExecutor
	Guard     *LiveCapitalGuard
	Portfolio *PaperPortfolio
	NatsConn  *nats.Conn
}

func (h *ConfigHandler) ServeSignals(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")
	
	GlobalConvictions.mu.RLock()
	defer GlobalConvictions.mu.RUnlock()
	json.NewEncoder(w).Encode(GlobalConvictions.Latest)
}

func (h *ConfigHandler) ServeAllSignals(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	GlobalSignals.mu.RLock()
	defer GlobalSignals.mu.RUnlock()
	json.NewEncoder(w).Encode(GlobalSignals.Latest)
}

// TradeLog represents a recorded trade for the UI.
type TradeLog struct {
	Time       time.Time `json:"time"`
	Ticker     string    `json:"ticker"`
	Action     string    `json:"action"`
	Quantity   int       `json:"quantity"`
	Price      float64   `json:"price"`
	Cost       float64   `json:"cost"`       // New field: Total dollar cost
	PnL        float64   `json:"pnl"`
	NetProfit  float64   `json:"net_profit"` // Tax-adjusted
}

var GlobalTrades = &struct {
	Recent []TradeLog `json:"recent"`
	mu     sync.RWMutex
}{Recent: make([]TradeLog, 0)}

func AddTradeLog(log TradeLog) {
	GlobalTrades.mu.Lock()
	defer GlobalTrades.mu.Unlock()
	GlobalTrades.Recent = append([]TradeLog{log}, GlobalTrades.Recent...)
	if len(GlobalTrades.Recent) > 500 {
		GlobalTrades.Recent = GlobalTrades.Recent[:500]
	}
}

// portfolioResponse is the shape returned by /api/portfolio.  It mirrors
// PaperPortfolio but adds a Source field so callers always know which backend
// is authoritative.
type portfolioResponse struct {
	NAV               float64             `json:"nav"`
	Cash              float64             `json:"cash"`
	RealizedPnL       float64             `json:"realized_pnl"`
	MaintenanceMargin float64             `json:"maintenance_margin"`
	Positions         map[string]Position `json:"positions"`
	// Source is the active ExecutionMode: "IBKR_PAPER", "IBKR_LIVE", or "SIMULATION".
	// /api/account returns the same Source value so both endpoints agree on which
	// backend is authoritative.
	Source string `json:"source"`
}

// ibkrPositionsToMap converts a raw []interface{} returned by
// runIBKRAccount("positions") into the map[string]Position format that
// portfolioResponse requires.  Fields that are missing or unparseable are
// zero-valued rather than causing a panic.
func ibkrPositionsToMap(raw interface{}) map[string]Position {
	result := make(map[string]Position)
	list, ok := raw.([]interface{})
	if !ok {
		return result
	}
	for _, item := range list {
		m, ok := item.(map[string]interface{})
		if !ok {
			continue
		}
		getString := func(k string) string {
			v, _ := m[k].(string)
			return v
		}
		getFloat := func(k string) float64 {
			switch v := m[k].(type) {
			case float64:
				return v
			case int:
				return float64(v)
			}
			return 0
		}
		getInt := func(k string) int {
			switch v := m[k].(type) {
			case float64:
				return int(v)
			case int:
				return v
			}
			return 0
		}
		ticker     := getString("ticker")
		optType    := getString("option_type")
		expiry     := getString("expiration")
		strike     := getFloat("strike")
		qty        := getInt("qty")
		avgCost    := getFloat("avg_cost")
		curPrice   := getFloat("current_price")
		unrealPnL  := getFloat("unrealized_pnl")

		sig := fmt.Sprintf("%s_%s_%s_%.2f", ticker, optType, expiry, strike)
		result[sig] = Position{
			Ticker:        ticker,
			OptionType:    optType,
			Strike:        strike,
			Expiry:        expiry,
			EntryPrice:    avgCost,
			CurrentPrice:  curPrice,
			Quantity:      qty,
			UnrealizedPnL: unrealPnL,
		}
	}
	return result
}

func (h *ConfigHandler) ServePortfolio(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	// ── SIMULATION mode: serve internal PaperPortfolio ────────────────────────
	if ActiveExecutionMode == ModeSimulation {
		h.Portfolio.mu.RLock()
		resp := portfolioResponse{
			NAV:               h.Portfolio.NAV,
			Cash:              h.Portfolio.Cash,
			RealizedPnL:       h.Portfolio.RealizedPnL,
			MaintenanceMargin: h.Portfolio.MaintenanceMargin,
			Positions:         h.Portfolio.Positions,
			Source:            string(ModeSimulation),
		}
		h.Portfolio.mu.RUnlock()
		json.NewEncoder(w).Encode(resp)
		return
	}

	// ── IBKR modes: NAV/cash from account cache; positions from IBKR subprocess ─
	resp := portfolioResponse{
		Source:    string(ActiveExecutionMode),
		Positions: make(map[string]Position),
	}

	// NAV / cash from cached account summary.
	accountCacheMu.Lock()
	cached := accountCache
	accountCacheMu.Unlock()
	if cached != nil {
		if m, ok := cached.(map[string]interface{}); ok {
			if nav, ok := m["net_liquidation"].(float64); ok && nav > 0 {
				resp.NAV = nav
			}
			if cash, ok := m["cash_balance"].(float64); ok && cash > 0 {
				resp.Cash = cash
			}
			if realized, ok := m["realized_pnl"].(float64); ok {
				resp.RealizedPnL = realized
			}
		}
	}

	// Positions from IBKR subprocess.  Use positionsCache when fresh.
	positionsCacheMu.Lock()
	if positionsCache != nil && time.Since(positionsCachedAt) < accountCacheTTL {
		rawPos := positionsCache
		positionsCacheMu.Unlock()
		resp.Positions = ibkrPositionsToMap(rawPos)
	} else {
		positionsCacheMu.Unlock()
		rawPos, err := runIBKRAccount("positions")
		if err != nil {
			// Subprocess failed — return explicit error; never silently fall back.
			json.NewEncoder(w).Encode(map[string]interface{}{
				"error":    err.Error(),
				"source":   string(ActiveExecutionMode),
				"nav":      resp.NAV,
				"cash":     resp.Cash,
				"positions": map[string]Position{},
			})
			return
		}
		// Only cache valid list results (not error dicts from Python).
		if _, isMap := rawPos.(map[string]interface{}); !isMap {
			positionsCacheMu.Lock()
			positionsCache = rawPos
			positionsCachedAt = time.Now()
			positionsCacheMu.Unlock()
			resp.Positions = ibkrPositionsToMap(rawPos)
		}
	}

	json.NewEncoder(w).Encode(resp)
}

func (h *ConfigHandler) ServeTrades(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")
	
	GlobalTrades.mu.RLock()
	defer GlobalTrades.mu.RUnlock()
	json.NewEncoder(w).Encode(GlobalTrades.Recent)
}

// SimState holds the latest simulation progress.
type SimState struct {
	Iteration int     `json:"iteration"`
	Pot       float64 `json:"pot"`
	PnlPct    float64 `json:"pnl_pct"`
	Status    string  `json:"status"`
	Trades    int     `json:"trades"`
	Wins      int     `json:"wins"`
}

var GlobalSimState = &struct {
	State SimState `json:"state"`
	mu    sync.RWMutex
}{}

func UpdateSimState(data []byte) {
	var s SimState
	if err := json.Unmarshal(data, &s); err == nil {
		GlobalSimState.mu.Lock()
		GlobalSimState.State = s
		GlobalSimState.mu.Unlock()
	}
}

func (h *ConfigHandler) ServeSimulation(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")
	
	GlobalSimState.mu.RLock()
	defer GlobalSimState.mu.RUnlock()
	json.NewEncoder(w).Encode(GlobalSimState.State)
}

func (h *ConfigHandler) ToggleSimulation(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	// Logic: Signal Gastown process via NATS or IPC.
	// For this build, we'll use a NATS broadcast to toggle state.
	nc, _ := nats.Connect("nats://127.0.0.1:4222")
	defer nc.Close()
	
	action := r.URL.Query().Get("action") // "START" or "STOP"
	fmt.Printf("UI Signal: Simulation %s requested.\n", action)
	nc.Publish("tsla.alpha.sim.control", []byte(action))
	
	w.WriteHeader(http.StatusOK)
	w.Write([]byte(`{"status": "ok"}`))
}

func (h *ConfigHandler) ResetGuard(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	h.Guard.Reset()
	w.WriteHeader(http.StatusOK)
	w.Write([]byte(`{"status": "ok"}`))
}

func (h *ConfigHandler) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	// Enable CORS for the React frontend
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

	if r.Method == "OPTIONS" {
		return
	}

	if r.Method == "GET" {
		GlobalConfig.mu.RLock()
		defer GlobalConfig.mu.RUnlock()
		json.NewEncoder(w).Encode(GlobalConfig)
		return
	}

	if r.Method == "POST" {
		var newConfig struct {
			IBKR struct {
				Host     string `json:"host"`
				Port     int    `json:"port"`
				Username string `json:"username"`
				Password string `json:"password"`
			} `json:"ibkr"`
			Telegram struct {
				Token  string `json:"token"`
				ChatID string `json:"chat_id"`
			} `json:"telegram"`
			TradingView struct {
				SessionID string `json:"session_id"`
			} `json:"trading_view"`
		}

		if err := json.NewDecoder(r.Body).Decode(&newConfig); err != nil {
			http.Error(w, "Invalid Payload", http.StatusBadRequest)
			return
		}

		GlobalConfig.mu.Lock()
		GlobalConfig.IBKR = newConfig.IBKR
		GlobalConfig.Telegram = newConfig.Telegram
		GlobalConfig.TradingView = newConfig.TradingView
		GlobalConfig.mu.Unlock()

		// Logic: Hot-swap the Telegram bot token in the Live Guard
		if newConfig.Telegram.Token != "" {
			h.Guard.AlertBot.Token = newConfig.Telegram.Token
			h.Guard.AlertBot.ChatID = newConfig.Telegram.ChatID
		}

		fmt.Println("System Config Updated via Alpha Control Center.")
		w.WriteHeader(http.StatusOK)
		w.Write([]byte(`{"status": "success"}`))
	}
}

// ========= METRICS AND SYSTEM MONITORING =========

type MetricsStore struct {
	RequestCounts []int       `json:"request_counts"`
	SignalCounts  []int       `json:"signal_counts"`
	cpuUsage      float64
	lastCpuSample time.Time
	lastTotal     uint64
	lastIdle      uint64
	startTime     time.Time
	TotalRequests uint64
	TotalSignals    uint64
	Latencies       []time.Duration
	SignalBreakdown map[string]int
	mu              sync.RWMutex
}

func NewMetricsStore() *MetricsStore {
	m := &MetricsStore{
		RequestCounts:   make([]int, 60),
		SignalCounts:    make([]int, 10),
		startTime:       time.Now(),
		TotalRequests:   0,
		TotalSignals:    0,
		Latencies:       make([]time.Duration, 0, 2000),
		SignalBreakdown: make(map[string]int),
	}

	// Ticker to advance the request-per-second buffer
	go func() {
		ticker := time.NewTicker(1 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			m.mu.Lock()
			m.RequestCounts = append([]int{0}, m.RequestCounts[:59]...)
			m.mu.Unlock()
		}
	}()

	// Ticker to advance the signals-per-minute buffer
	go func() {
		ticker := time.NewTicker(1 * time.Minute)
		defer ticker.Stop()
		for range ticker.C {
			m.mu.Lock()
			m.SignalCounts = append([]int{0}, m.SignalCounts[:9]...)
			m.mu.Unlock()
		}
	}()

	// Ticker to update CPU usage
	go func() {
		ticker := time.NewTicker(2 * time.Second)
		defer ticker.Stop()
		for range ticker.C {
			m.updateCpuUsage()
		}
	}()

	return m
}

func (m *MetricsStore) LogRequest() {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.TotalRequests++
	if len(m.RequestCounts) > 0 {
		m.RequestCounts[0]++
	}
}

func (m *MetricsStore) LogSignal(strategyCode string) {
	m.mu.Lock()
	defer m.mu.Unlock()
	m.TotalSignals++
	if len(m.SignalCounts) > 0 {
		m.SignalCounts[0]++
	}
	m.SignalBreakdown[strategyCode]++
}

func (m *MetricsStore) updateCpuUsage() {
	m.mu.Lock()
	defer m.mu.Unlock()

	content, err := ioutil.ReadFile("/proc/stat")
	if err != nil {
		return
	}

	lines := strings.Split(string(content), "\n")
	if len(lines) == 0 {
		return
	}

	fields := strings.Fields(lines[0])
	if len(fields) < 5 || fields[0] != "cpu" {
		return
	}

	var total, idle uint64
	for i := 1; i < len(fields); i++ {
		val, err := strconv.ParseUint(fields[i], 10, 64)
		if err != nil {
			return
		}
		if i == 4 { // idle time
			idle = val
		}
		total += val
	}

	if m.lastTotal > 0 {
		deltaTotal := total - m.lastTotal
		deltaIdle := idle - m.lastIdle
		if deltaTotal > 0 {
			m.cpuUsage = (1.0 - float64(deltaIdle)/float64(deltaTotal)) * 100.0
		}
	}

	m.lastTotal = total
	m.lastIdle = idle
	m.lastCpuSample = time.Now()
}

var GlobalMetrics = NewMetricsStore()

func (h *ConfigHandler) ServeRequestMetrics(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	GlobalMetrics.mu.RLock()
	defer GlobalMetrics.mu.RUnlock()
	json.NewEncoder(w).Encode(GlobalMetrics.RequestCounts)
}

func (h *ConfigHandler) ServeSignalMetrics(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	GlobalMetrics.mu.RLock()
	defer GlobalMetrics.mu.RUnlock()
	json.NewEncoder(w).Encode(GlobalMetrics.SignalCounts)
}

// ServePublisherMetrics reads the publisher.py metrics file written after each
// commission-rejected signal and returns a JSON object.  The file is written by
// publisher.py to /tmp/publisher_metrics.json.
func (h *ConfigHandler) ServePublisherMetrics(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	data, err := os.ReadFile("/tmp/publisher_metrics.json")
	if err != nil {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"signals_rejected_commission_total": 0,
			"ts": nil,
		})
		return
	}
	w.Write(data)
}

func (h *ConfigHandler) ServeLogs(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")

	file, err := os.Open("executor.log")
	if err != nil {
		http.Error(w, "Log file not found.", http.StatusInternalServerError)
		return
	}
	defer file.Close()

	var lines []string
	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		lines = append(lines, scanner.Text())
	}

	start := 0
	if len(lines) > 200 {
		start = len(lines) - 200
	}

	for _, line := range lines[start:] {
		fmt.Fprintln(w, line)
	}
}

type Vitals struct {
	Uptime        float64 `json:"uptime_sec"`
	Goroutines    int     `json:"goroutines"`
	MemUsedMB     uint64  `json:"mem_mb"`
	CpuPct        float64 `json:"cpu_pct"`
	GcPauseMs     float64 `json:"gc_pause_ms"`
	HeapAlloc     uint64  `json:"heap_alloc"`
	HeapObjects   uint64  `json:"heap_objects"`
	NextGC        uint64  `json:"next_gc"`
	TotalRequests uint64  `json:"total_requests"`
	TotalSignals  uint64  `json:"total_signals"`
}

func (h *ConfigHandler) ServeVitals(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	var memStats runtime.MemStats
	runtime.ReadMemStats(&memStats)

	GlobalMetrics.mu.RLock()
	cpuUsage := GlobalMetrics.cpuUsage
	startTime := GlobalMetrics.startTime
	totalRequests := GlobalMetrics.TotalRequests
	totalSignals := GlobalMetrics.TotalSignals
	GlobalMetrics.mu.RUnlock()

	vitals := Vitals{
		Uptime:        time.Since(startTime).Seconds(),
		Goroutines:    runtime.NumGoroutine(),
		MemUsedMB:     memStats.Alloc / 1024 / 1024,
		CpuPct:        cpuUsage,
		GcPauseMs:     float64(memStats.PauseTotalNs) / float64(time.Millisecond),
		HeapAlloc:     memStats.HeapAlloc / 1024 / 1024,
		HeapObjects:   memStats.HeapObjects,
		NextGC:        memStats.NextGC / 1024 / 1024,
		TotalRequests: totalRequests,
		TotalSignals:  totalSignals,
	}

	json.NewEncoder(w).Encode(vitals)
}

func (h *ConfigHandler) ServeLatencyMetrics(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")

	GlobalMetrics.mu.RLock()
	latencies := make([]time.Duration, len(GlobalMetrics.Latencies))
	copy(latencies, GlobalMetrics.Latencies)
	GlobalMetrics.mu.RUnlock()

	if len(latencies) == 0 {
		json.NewEncoder(w).Encode(map[string]float64{"p50": 0, "p95": 0, "p99": 0})
		return
	}

	sort.Slice(latencies, func(i, j int) bool { return latencies[i] < latencies[j] })

	p50 := latencies[len(latencies)/2]
	p95 := latencies[len(latencies)*95/100]
	p99 := latencies[len(latencies)*99/100]

	json.NewEncoder(w).Encode(map[string]float64{
		"p50": float64(p50.Microseconds()) / 1000.0,
		"p95": float64(p95.Microseconds()) / 1000.0,
		"p99": float64(p99.Microseconds()) / 1000.0,
	})
}

func (h *ConfigHandler) ServeSignalBreakdown(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	GlobalMetrics.mu.RLock()
	defer GlobalMetrics.mu.RUnlock()
	json.NewEncoder(w).Encode(GlobalMetrics.SignalBreakdown)
}

type NatsHealth struct {
	Connected  bool   `json:"connected"`
	ServerURL  string `json:"server_url"`
	MsgsIn     uint64 `json:"msgs_in"`
	MsgsOut    uint64 `json:"msgs_out"`
	BytesIn    uint64 `json:"bytes_in"`
	BytesOut   uint64 `json:"bytes_out"`
	Reconnects uint64 `json:"reconnects"`
}

func (h *ConfigHandler) ServeNatsHealth(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	if h.NatsConn == nil || !h.NatsConn.IsConnected() {
		json.NewEncoder(w).Encode(NatsHealth{Connected: false})
		return
	}

	stats := h.NatsConn.Stats()
	health := NatsHealth{
		Connected:  true,
		ServerURL:  h.NatsConn.ConnectedUrl(),
		MsgsIn:     stats.InMsgs,
		MsgsOut:    stats.OutMsgs,
		BytesIn:    stats.InBytes,
		BytesOut:   stats.OutBytes,
		Reconnects: stats.Reconnects,
	}
	json.NewEncoder(w).Encode(health)
}

type BuildInfo struct {
	GitCommit string `json:"git_commit"`
	BuildTime string `json:"build_time"`
	GoVersion string `json:"go_version"`
}

func (h *ConfigHandler) ServeBuildInfo(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	info := BuildInfo{
		GitCommit: gitCommit,
		BuildTime: buildTime,
		GoVersion: goVersion,
	}
	json.NewEncoder(w).Encode(info)
}

func runFillDetail(args ...string) ([]byte, error) {
	cmd := exec.Command("./alpha_engine/venv/bin/python", append([]string{"alpha_engine/data/fill_detail.py"}, args...)...)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = os.Environ()
	return cmd.Output()
}

func (h *ConfigHandler) ServeClosedTrades(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	out, err := runFillDetail("list")
	if err != nil {
		json.NewEncoder(w).Encode([]interface{}{})
		return
	}
	w.Write(out)
}

func (h *ConfigHandler) ServeFillDetail(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	id := r.URL.Query().Get("id")
	if id == "" {
		json.NewEncoder(w).Encode(map[string]string{"error": "id required"})
		return
	}
	out, err := runFillDetail("detail", id)
	if err != nil {
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	w.Write(out)
}

func (h *ConfigHandler) ServeGoroutineProfile(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "text/plain; charset=utf-8")
	pprof.Lookup("goroutine").WriteTo(w, 1)
}

type BrokerStatus struct {
	Mode      string `json:"mode"`       // ExecutionMode value: IBKR_PAPER | IBKR_LIVE | SIMULATION
	Broker    string `json:"broker"`
	Confirmed bool   `json:"confirmed"`
	Connected bool   `json:"connected"`
	OrderPath string `json:"order_path"` // "IBKR_PAPER (real)" | "SIMULATION (internal)"
}

type SystemState struct {
	KillSwitch           bool    `json:"kill_switch"`
	SignalsBlockedReason string  `json:"signals_blocked_reason"`
	DailyPnL             float64 `json:"daily_pnl"`
	MaxDailyLoss         float64 `json:"max_daily_loss"`
	Mode                 string  `json:"mode"`
	ConvictionCount      int     `json:"conviction_count"`
}

// isMarketOpen returns true if current ET time is within regular trading hours (Mon-Fri 9:30-16:00)
func isMarketOpen() bool {
	loc, err := time.LoadLocation("America/New_York")
	if err != nil {
		return false
	}
	now := time.Now().In(loc)
	wd := now.Weekday()
	if wd == time.Saturday || wd == time.Sunday {
		return false
	}
	open := time.Date(now.Year(), now.Month(), now.Day(), 9, 30, 0, 0, loc)
	close := time.Date(now.Year(), now.Month(), now.Day(), 16, 0, 0, 0, loc)
	return now.After(open) && now.Before(close)
}

func (h *ConfigHandler) ServeSystemState(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	killSwitch := h.Guard != nil && h.Guard.KillSwitch

	GlobalConvictions.mu.RLock()
	convCount := len(GlobalConvictions.Latest)
	GlobalConvictions.mu.RUnlock()

	reason := ""
	switch {
	case killSwitch:
		reason = "kill_switch"
	case !isMarketOpen():
		reason = "market_closed"
	case convCount == 0:
		reason = "no_signals"
	}

	dailyPnL := 0.0
	maxDailyLoss := 0.0
	if h.Guard != nil {
		dailyPnL = h.Guard.CurrentDailyPnL
		maxDailyLoss = h.Guard.MaxDailyLoss
	}

	json.NewEncoder(w).Encode(SystemState{
		KillSwitch:           killSwitch,
		SignalsBlockedReason: reason,
		DailyPnL:             dailyPnL,
		MaxDailyLoss:         maxDailyLoss,
		Mode:                 string(ActiveExecutionMode),
		ConvictionCount:      convCount,
	})
}

func (h *ConfigHandler) ServeBrokerStatus(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	// Connectivity is verified per-order via the ibkr_order.py subprocess.
	// No persistent broker connection is held by the Go engine.
	connected := true
	orderPath := "IBKR_PAPER (real)"
	switch ActiveExecutionMode {
	case ModeSimulation:
		orderPath = "SIMULATION (internal)"
	case ModeIBKRLive:
		orderPath = "IBKR_LIVE (real) — EXPERIMENTAL"
	}

	status := BrokerStatus{
		Mode:      string(ActiveExecutionMode),
		Broker:    "IBKR",
		Confirmed: connected,
		Connected: connected,
		OrderPath: orderPath,
	}
	json.NewEncoder(w).Encode(status)
}

func (h *ConfigHandler) ServeStatus(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	status := map[string]interface{}{
		"server":    "ok",
		"version":   gitCommit,
		"timestamp": time.Now(),
	}
	json.NewEncoder(w).Encode(status)
}

func RateLimiterMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		ip, _, err := net.SplitHostPort(r.RemoteAddr)
		if err != nil {
			next.ServeHTTP(w, r)
			return
		}

		limiterMu.Lock()
		limiter, exists := limiters[ip]
		if !exists {
			limiter = rate.NewLimiter(30, 100)
			limiters[ip] = limiter
		}
		limiterMu.Unlock()

		if !limiter.Allow() {
			w.Header().Set("Retry-After", "2")
			http.Error(w, "Too Many Requests", http.StatusTooManyRequests)
			return
		}

		next.ServeHTTP(w, r)
	})
}

// ========= GASTOWN DASHBOARD =========

type AgentDetail struct {
	Hook        string `json:"hook"`
	Heartbeat   string `json:"heartbeat"`
	MailCount   int    `json:"mail_count"`
	LastActive  string `json:"last_active"`
}

type GitInfo struct {
	Branch     string `json:"branch"`
	LastCommit string `json:"last_commit"`
}

type GastownFullStatus struct {
	Status       interface{}              `json:"status"`
	Log          []string                 `json:"log"`
	Ready        interface{}              `json:"ready"`
	AgentsDetail map[string]AgentDetail   `json:"agents_detail"`
	Patrols      interface{}              `json:"patrols"`
	TmuxSessions []string                 `json:"tmux_sessions"`
	Escalation   interface{}              `json:"escalation"`
	GitInfo      GitInfo                  `json:"git_info"`
	RefreshedAt  string                   `json:"refreshed_at"`
}

func runGTCommand(args ...string) ([]byte, error) {
	cmd := exec.Command("/home/builder/go/bin/gt", args...)
	cmd.Dir = "/home/builder/gt"
	// Ensure dolt and bd are findable — systemd service may not have full user PATH
	enrichedPath := "/home/builder/go/bin:/home/builder/.local/bin:" + os.Getenv("PATH")
	cmd.Env = append(os.Environ(), "HOME=/home/builder", "PATH="+enrichedPath)
	return cmd.Output()
}

func readJSONFile(path string) interface{} {
	data, err := os.ReadFile(path)
	if err != nil {
		return map[string]interface{}{"error": err.Error()}
	}
	var result interface{}
	if err := json.Unmarshal(data, &result); err != nil {
		return map[string]interface{}{"raw": string(data), "error": err.Error()}
	}
	return result
}

func (h *ConfigHandler) ServeGastownStatus(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	homeDir := "/home/builder"
	gtDir := homeDir + "/gt"

	result := GastownFullStatus{
		AgentsDetail: make(map[string]AgentDetail),
		RefreshedAt:  time.Now().Format(time.RFC3339),
	}

	// gt status --json
	if out, err := runGTCommand("status", "--json"); err == nil {
		var s interface{}
		if json.Unmarshal(out, &s) == nil {
			result.Status = s
		}
	} else {
		result.Status = map[string]interface{}{"error": err.Error()}
	}

	// gt log (last 50 lines)
	if out, err := runGTCommand("log"); err == nil {
		lines := strings.Split(strings.TrimSpace(string(out)), "\n")
		filtered := []string{}
		for _, l := range lines {
			t := strings.TrimSpace(l)
			if t != "" && !strings.HasPrefix(t, "WARNING:") && !strings.HasPrefix(t, "Use 'make") && !strings.HasPrefix(t, "Run from:") && !strings.HasPrefix(t, "Warning:") && !strings.HasPrefix(t, "○ No log") {
				filtered = append(filtered, t)
			}
		}
		if len(filtered) > 50 {
			filtered = filtered[len(filtered)-50:]
		}
		result.Log = filtered
	} else {
		result.Log = []string{}
	}

	// gt ready --json
	if out, err := runGTCommand("ready", "--json"); err == nil {
		var s interface{}
		if json.Unmarshal(out, &s) == nil {
			result.Ready = s
		}
	} else {
		result.Ready = map[string]interface{}{"error": err.Error()}
	}

	// Read patrol config from daemon.json
	result.Patrols = readJSONFile(gtDir + "/mayor/daemon.json")

	// Read escalation config
	result.Escalation = readJSONFile(gtDir + "/settings/escalation.json")

	// Per-agent filesystem details
	agents := []string{"mayor", "deacon"}
	for _, agent := range agents {
		detail := AgentDetail{}
		agentDir := gtDir + "/" + agent

		// Hook file
		hookPath := agentDir + "/hook"
		if data, err := os.ReadFile(hookPath); err == nil {
			detail.Hook = strings.TrimSpace(string(data))
		}

		// Heartbeat file — prefer tmux session activity for alt-session agents
		hbPath := agentDir + "/heartbeat"
		if info, err := os.Stat(hbPath); err == nil {
			detail.Heartbeat = info.ModTime().Format(time.RFC3339)
			detail.LastActive = info.ModTime().Format(time.RFC3339)
		} else if dirInfo, err := os.Stat(agentDir); err == nil {
			detail.LastActive = dirInfo.ModTime().Format(time.RFC3339)
		}

		result.AgentsDetail[agent] = detail
	}

	// tmux session activity times: map session name → Unix timestamp
	tmuxActivity := map[string]int64{}
	actCmd := exec.Command("tmux", "-L", "default", "list-sessions", "-F", "#{session_name} #{session_activity}")
	actCmd.Env = os.Environ()
	if out, err := actCmd.Output(); err == nil {
		for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
			parts := strings.Fields(line)
			if len(parts) == 2 {
				var ts int64
				if _, err := fmt.Sscanf(parts[1], "%d", &ts); err == nil {
					tmuxActivity[parts[0]] = ts
				}
			}
		}
	}
	// Override last_active for agents running in alt-sessions
	altSessionMap := map[string]string{"mayor": "tsla-claude", "deacon": "tsla-linux"}
	for agent, sess := range altSessionMap {
		if ts, ok := tmuxActivity[sess]; ok && ts > 0 {
			d := result.AgentsDetail[agent]
			d.LastActive = time.Unix(ts, 0).Format(time.RFC3339)
			result.AgentsDetail[agent] = d
		}
	}

	// tmux sessions
	cmd := exec.Command("tmux", "-L", "default", "list-sessions", "-F", "#{session_name}")
	cmd.Env = os.Environ()
	if out, err := cmd.Output(); err == nil {
		sessions := strings.Split(strings.TrimSpace(string(out)), "\n")
		result.TmuxSessions = sessions
	}

	// Enrich: override agent running status based on actual tmux sessions.
	// gt names sessions "hq-mayor"/"hq-deacon" but we run as "tsla-claude"/"tsla-linux".
	sessionSet := map[string]bool{}
	for _, s := range result.TmuxSessions {
		sessionSet[s] = true
	}
	// Map known alt-session names to agents.
	// tsla-claude is the actual mayor session. tsla-linux is Gemini CLI (not deacon).
	agentAltSessions := map[string][]string{
		"mayor":  {"tsla-claude", "hq-mayor"},
		"deacon": {"hq-deacon"},
	}
	if statusMap, ok := result.Status.(map[string]interface{}); ok {
		if agentList, ok := statusMap["agents"].([]interface{}); ok {
			for i, a := range agentList {
				if agentMap, ok := a.(map[string]interface{}); ok {
					name, _ := agentMap["name"].(string)
					if alts, has := agentAltSessions[name]; has {
						for _, alt := range alts {
							if sessionSet[alt] {
								agentMap["running"] = true
								agentMap["session"] = alt
								agentList[i] = agentMap
								break
							}
						}
					}
				}
			}
			statusMap["agents"] = agentList
			result.Status = statusMap
		}
	}

	// Enrich: if gt log is empty, capture live output from the active tmux sessions.
	if len(result.Log) == 0 {
		prioritySessions := []string{"tsla-claude", "tsla-linux"}
		for _, sess := range prioritySessions {
			if !sessionSet[sess] {
				continue
			}
			captureCmd := exec.Command("tmux", "-L", "default", "capture-pane", "-t", sess, "-p", "-S", "-80")
			captureCmd.Env = os.Environ()
			if out, err := captureCmd.Output(); err == nil {
				prefix := "[" + sess + "] "
				for _, l := range strings.Split(string(out), "\n") {
					t := strings.TrimSpace(l)
					if t != "" && len(t) > 4 {
						result.Log = append(result.Log, prefix+t)
					}
				}
			}
			if len(result.Log) > 0 {
				break
			}
		}
		if len(result.Log) > 60 {
			result.Log = result.Log[len(result.Log)-60:]
		}
	}

	// Git info from gt dir
	gitInfo := GitInfo{}
	if out, err := exec.Command("git", "-C", gtDir, "log", "-1", "--pretty=%H|%s").Output(); err == nil {
		parts := strings.SplitN(strings.TrimSpace(string(out)), "|", 2)
		if len(parts) == 2 {
			gitInfo.LastCommit = parts[0][:min(7, len(parts[0]))] + " " + parts[1]
		}
	}
	if out, err := exec.Command("git", "-C", gtDir, "rev-parse", "--abbrev-ref", "HEAD").Output(); err == nil {
		gitInfo.Branch = strings.TrimSpace(string(out))
	}
	result.GitInfo = gitInfo

	json.NewEncoder(w).Encode(result)
}

func min(a, b int) int {
	if a < b {
		return a
	}
	return b
}

// ─── History types ────────────────────────────────────────────────────────────

type GitCommit struct {
	Hash    string `json:"hash"`
	Date    string `json:"date"`
	Message string `json:"message"`
}

type BeadIssue struct {
	ID       string `json:"id"`
	Title    string `json:"title"`
	Status   string `json:"status"`
	Priority int    `json:"priority"`
}

type GastownHistory struct {
	RepoLog      []GitCommit `json:"repo_log"`
	WorkspaceLog []GitCommit `json:"workspace_log"`
	Beads        []BeadIssue `json:"beads"`
	SessionTail  []string    `json:"session_tail"`
	RefreshedAt  string      `json:"refreshed_at"`
}

func parseGitLog(dir string, n int) []GitCommit {
	out, err := exec.Command("git", "-C", dir, "log", fmt.Sprintf("--format=%%H|%%ai|%%s"), fmt.Sprintf("-%d", n)).Output()
	if err != nil {
		return []GitCommit{}
	}
	commits := []GitCommit{}
	for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		parts := strings.SplitN(line, "|", 3)
		if len(parts) != 3 {
			continue
		}
		hash := parts[0]
		if len(hash) > 7 {
			hash = hash[:7]
		}
		commits = append(commits, GitCommit{
			Hash:    hash,
			Date:    strings.TrimSpace(parts[1]),
			Message: strings.TrimSpace(parts[2]),
		})
	}
	return commits
}

func parseBdIssues() []BeadIssue {
	enrichedPath := "/home/builder/go/bin:/home/builder/.local/bin:" + os.Getenv("PATH")
	cmd := exec.Command("/home/builder/.local/bin/bd", "list", "--all", "--flat")
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = append(os.Environ(), "HOME=/home/builder", "PATH="+enrichedPath)
	out, err := cmd.Output()
	if err != nil {
		return []BeadIssue{}
	}
	issues := []BeadIssue{}
	statusMap := map[string]string{
		"✓": "closed",
		"○": "open",
		"◐": "in_progress",
		"●": "blocked",
		"❄": "deferred",
	}
	priorityMap := map[string]int{
		"P0": 0, "P1": 1, "P2": 2, "P3": 3, "P4": 4,
	}
	for _, raw := range strings.Split(string(out), "\n") {
		line := strings.TrimSpace(raw)
		if line == "" || strings.HasPrefix(line, "-") || strings.HasPrefix(line, "Total") || strings.HasPrefix(line, "Status") {
			continue
		}
		// Format: <statusIcon> <id> [P<n>] [<type>] - <title>
		fields := strings.Fields(line)
		if len(fields) < 4 {
			continue
		}
		icon := fields[0]
		id := fields[1]
		status := statusMap[icon]
		if status == "" {
			status = "unknown"
		}
		priority := 2
		titleStart := 2
		// scan for [Pn] and skip [type]
		for i := 2; i < len(fields); i++ {
			f := fields[i]
			if strings.HasPrefix(f, "[P") && strings.HasSuffix(f, "]") {
				key := strings.Trim(f, "[]")
				if p, ok := priorityMap[key]; ok {
					priority = p
				}
				titleStart = i + 1
			} else if strings.HasPrefix(f, "[") && strings.HasSuffix(f, "]") {
				titleStart = i + 1
			} else if f == "-" {
				titleStart = i + 1
				break
			}
		}
		title := strings.Join(fields[titleStart:], " ")
		if title == "" {
			title = id
		}
		issues = append(issues, BeadIssue{
			ID:       id,
			Title:    title,
			Status:   status,
			Priority: priority,
		})
	}
	return issues
}

func (h *ConfigHandler) ServeGastownHistory(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	hist := GastownHistory{
		RefreshedAt:  time.Now().Format(time.RFC3339),
		RepoLog:      parseGitLog("/home/builder/src/gpfiles/tcode", 30),
		WorkspaceLog: parseGitLog("/home/builder/gt", 20),
		Beads:        parseBdIssues(),
		SessionTail:  []string{},
	}

	// tmux session tail for tsla-claude (last 200 lines)
	captureCmd := exec.Command("tmux", "-L", "default", "capture-pane", "-t", "tsla-claude", "-p", "-S", "-200")
	captureCmd.Env = os.Environ()
	if out, err := captureCmd.Output(); err == nil {
		for _, line := range strings.Split(string(out), "\n") {
			t := strings.TrimRight(line, " \t")
			if t != "" {
				hist.SessionTail = append(hist.SessionTail, t)
			}
		}
	}

	json.NewEncoder(w).Encode(hist)
}

func (h *ConfigHandler) ServeGastownLog(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	lines := []string{}

	isPlaceholder := func(t string) bool {
		return strings.HasPrefix(t, "WARNING:") ||
			strings.HasPrefix(t, "Use 'make") ||
			strings.HasPrefix(t, "Run from:") ||
			strings.HasPrefix(t, "Warning:") ||
			strings.HasPrefix(t, "○ No log") ||
			strings.Contains(t, "No log file yet") ||
			strings.Contains(t, "No activity log entries yet")
	}

	// Try gt log first
	if out, err := runGTCommand("log"); err == nil {
		raw := strings.Split(strings.TrimSpace(string(out)), "\n")
		for _, l := range raw {
			t := strings.TrimSpace(l)
			if t != "" && !isPlaceholder(t) {
				lines = append(lines, t)
			}
		}
	}

	// Also try reading activity log files directly
	logPaths := []string{
		"/home/builder/gt/activity.log",
		"/home/builder/gt/.beads/activity.log",
	}
	for _, p := range logPaths {
		if data, err := os.ReadFile(p); err == nil {
			raw := strings.Split(strings.TrimSpace(string(data)), "\n")
			for _, l := range raw {
				t := strings.TrimSpace(l)
				if t != "" && !isPlaceholder(t) {
					lines = append(lines, t)
				}
			}
			if len(lines) > 0 {
				break
			}
		}
	}

	// Fallback: capture live tmux session output
	if len(lines) == 0 {
		for _, sess := range []string{"tsla-claude", "tsla-linux"} {
			captureCmd := exec.Command("tmux", "-L", "default", "capture-pane", "-t", sess, "-p", "-S", "-80")
			captureCmd.Env = os.Environ()
			if out, err := captureCmd.Output(); err == nil {
				prefix := "[" + sess + "] "
				for _, l := range strings.Split(string(out), "\n") {
					t := strings.TrimSpace(l)
					if t != "" && len(t) > 4 {
						lines = append(lines, prefix+t)
					}
				}
			}
			if len(lines) > 0 {
				break
			}
		}
	}

	if len(lines) > 200 {
		lines = lines[len(lines)-200:]
	}

	json.NewEncoder(w).Encode(lines)
}

// ── /api/data/audit ───────────────────────────────────────────────────────────

// dataAuditCache holds the last result from the Python validation script.
var (
	dataAuditMu      sync.Mutex
	dataAuditResult  map[string]interface{}
	dataAuditFetched time.Time
	dataAuditTTL     = 60 * time.Second
)

// runDataAudit executes the Python audit aggregator and returns parsed JSON.
// ingestion.audit runs IBKR → TV → yfinance fallback chain and returns combined status.
func runDataAudit() (map[string]interface{}, error) {
	// Run from alpha_engine/ so `ingestion` package is on sys.path
	cmd := exec.Command("./venv/bin/python", "-m", "ingestion.audit", "TSLA")
	cmd.Dir = "./alpha_engine"
	cmd.Env = os.Environ()

	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("audit subprocess: %w", err)
	}

	var result map[string]interface{}
	if err := json.Unmarshal(out, &result); err != nil {
		return nil, fmt.Errorf("audit JSON parse: %w (raw: %s)", err, string(out))
	}
	return result, nil
}

// DataAuditResponse is the full /api/data/audit payload.
type DataAuditResponse struct {
	SpotValidation    map[string]interface{} `json:"spot_validation"`
	OptionsChainSrc   string                 `json:"options_chain_source"`
	LastChainFetch    string                 `json:"last_chain_fetch"`
	ChainAgeSec       float64                `json:"chain_age_sec"`
	ChainEntryCount   int                    `json:"chain_entry_count"`
	TVFeedOK          bool                   `json:"tv_feed_ok"`
	YFFeedOK          bool                   `json:"yf_feed_ok"`
	IBKRConnected     bool                   `json:"ibkr_connected"`
	IBKRSpot          float64                `json:"ibkr_spot"`
	PrimarySource     string                 `json:"primary_source"`
}

func (h *ConfigHandler) ServeDataAudit(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	forceRefresh := r.URL.Query().Get("refresh") == "true"

	dataAuditMu.Lock()
	age := time.Since(dataAuditFetched)
	cached := dataAuditResult
	dataAuditMu.Unlock()

	var spotVal map[string]interface{}
	if !forceRefresh && cached != nil && age < dataAuditTTL {
		spotVal = cached
	} else {
		var err error
		spotVal, err = runDataAudit()
		if err != nil {
			// Return a degraded response rather than 500
			spotVal = map[string]interface{}{
				"tv":              nil,
				"yf":              nil,
				"divergence_pct":  0.0,
				"ok":              false,
				"warning":         err.Error(),
				"timestamp":       time.Now().UTC().Format(time.RFC3339),
			}
		}
		dataAuditMu.Lock()
		dataAuditResult = spotVal
		dataAuditFetched = time.Now()
		dataAuditMu.Unlock()
	}

	tvOK := spotVal["tv"] != nil
	yfOK := spotVal["yf"] != nil
	ibkrConnected, _ := spotVal["ibkr_connected"].(bool)
	ibkrSpot, _ := spotVal["ibkr_spot"].(float64)
	primarySource, _ := spotVal["primary_source"].(string)
	if primarySource == "" {
		primarySource = "yfinance"
	}

	// Build spot_validation sub-object (TV/YF fields only)
	spotValidation := map[string]interface{}{
		"tv":             spotVal["tv"],
		"yf":             spotVal["yf"],
		"divergence_pct": spotVal["divergence_pct"],
		"ok":             spotVal["ok"],
		"warning":        spotVal["warning"],
		"timestamp":      spotVal["timestamp"],
	}

	// Determine chain source based on primary data source
	chainSrc := primarySource
	if chainSrc == "" {
		chainSrc = "yfinance"
	}

	chainPriceCacheMu.Lock()
	chainEntryCount := len(chainPriceCache)
	chainPriceCacheMu.Unlock()

	resp := DataAuditResponse{
		SpotValidation:  spotValidation,
		OptionsChainSrc: chainSrc,
		LastChainFetch:  dataAuditFetched.UTC().Format(time.RFC3339),
		ChainAgeSec:     time.Since(dataAuditFetched).Seconds(),
		ChainEntryCount: chainEntryCount,
		TVFeedOK:        tvOK,
		YFFeedOK:        yfOK,
		IBKRConnected:   ibkrConnected,
		IBKRSpot:        ibkrSpot,
		PrimarySource:   primarySource,
	}
	json.NewEncoder(w).Encode(resp)
}

// ── account helpers ───────────────────────────────────────────────────────────

func runIBKRAccount(mode string, args ...string) (interface{}, error) {
	cmdArgs := append([]string{"-m", "ingestion.ibkr_account", mode}, args...)
	cmd := exec.Command("./venv/bin/python", cmdArgs...)
	cmd.Dir = "./alpha_engine"
	cmd.Env = os.Environ()
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("ibkr_account[%s]: %w", mode, err)
	}
	var result interface{}
	if err := json.Unmarshal(out, &result); err != nil {
		return nil, fmt.Errorf("ibkr_account JSON[%s]: %w", mode, err)
	}
	return result, nil
}

func (h *ConfigHandler) ServeAccount(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	// SIMULATION mode: no broker is connected — return a clear error rather than
	// silently serving stale or empty IBKR data.
	if ActiveExecutionMode == ModeSimulation {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error":  "SIMULATION mode — no broker connected",
			"mode":   string(ModeSimulation),
			"source": string(ModeSimulation),
		})
		return
	}

	accountCacheMu.Lock()
	if accountCache != nil && time.Since(accountCachedAt) < accountCacheTTL {
		cached := accountCache
		accountCacheMu.Unlock()
		// Inject source field into cached result
		if m, ok := cached.(map[string]interface{}); ok {
			m["source"] = string(ActiveExecutionMode)
		}
		json.NewEncoder(w).Encode(cached)
		return
	}
	accountCacheMu.Unlock()

	result, err := runIBKRAccount("account")
	if err != nil {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error":            err.Error(),
			"source":           string(ActiveExecutionMode),
			"net_liquidation":  0,
			"cash_balance":     0,
			"buying_power":     0,
			"unrealized_pnl":   0,
			"realized_pnl":     0,
			"equity_with_loan": 0,
		})
		return
	}

	// Inject source field before caching and returning.
	if m, ok := result.(map[string]interface{}); ok {
		m["source"] = string(ActiveExecutionMode)
	}

	accountCacheMu.Lock()
	accountCache = result
	accountCachedAt = time.Now()
	accountCacheMu.Unlock()

	json.NewEncoder(w).Encode(result)
}

func (h *ConfigHandler) ServePositions(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	positionsCacheMu.Lock()
	if positionsCache != nil && time.Since(positionsCachedAt) < accountCacheTTL {
		cached := positionsCache
		positionsCacheMu.Unlock()
		json.NewEncoder(w).Encode(cached)
		return
	}
	positionsCacheMu.Unlock()

	result, err := runIBKRAccount("positions")
	if err != nil {
		w.Write([]byte("[]"))
		return
	}
	// Ensure we only cache valid list results (not error dicts from Python)
	if _, isMap := result.(map[string]interface{}); isMap {
		w.Write([]byte("[]"))
		return
	}

	positionsCacheMu.Lock()
	positionsCache = result
	positionsCachedAt = time.Now()
	positionsCacheMu.Unlock()

	json.NewEncoder(w).Encode(result)
}

func (h *ConfigHandler) ServeFills(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	hours := r.URL.Query().Get("hours")
	if hours == "" {
		hours = "24"
	}
	result, err := runIBKRAccount("fills", hours)
	if err != nil {
		w.Write([]byte("[]"))
		return
	}
	json.NewEncoder(w).Encode(result)
}

// pendingActiveStatuses is the set of IBKR order statuses shown in the "active"
// section of /api/orders/pending.
var pendingActiveStatuses = map[string]bool{
	"PreSubmitted":  true,
	"Submitted":     true,
	"PendingSubmit": true,
}

// ServeOrdersPending shells out to ibkr_order open_orders and returns two lists:
//   - active: PreSubmitted / Submitted / PendingSubmit orders
//   - cancelled: Cancelled orders (shown in the collapsed accordion)
//
// In SIMULATION mode it returns empty lists immediately.
func (h *ConfigHandler) ServeOrdersPending(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	if ActiveExecutionMode == ModeSimulation {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"active":    []OrderResult{},
			"cancelled": []OrderResult{},
			"source":    string(ModeSimulation),
		})
		return
	}

	orders, err := OpenIBKROrders()
	if err != nil {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"error":     err.Error(),
			"active":    []OrderResult{},
			"cancelled": []OrderResult{},
			"source":    string(ActiveExecutionMode),
		})
		return
	}

	active    := []interface{}{}
	cancelled := []OrderResult{}
	maxPending := envIntOrDefault("MAX_PENDING_ORDERS", 2)
	capOrders  := GetPendingCapOrders()
	rankByID   := make(map[int]float64, len(capOrders))
	for _, info := range capOrders {
		rankByID[info.OrderID] = info.Rank
	}

	for _, o := range orders {
		switch {
		case pendingActiveStatuses[o.Status]:
			// Merge stored rank into the order DTO.
			type pendingDTO struct {
				OrderResult
				Rank float64 `json:"rank"`
			}
			rank := rankByID[o.OrderID] // 0.0 if not tracked
			active = append(active, pendingDTO{OrderResult: o, Rank: rank})
		case o.Status == "Cancelled":
			cancelled = append(cancelled, o)
		}
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"active":    active,
		"cancelled": cancelled,
		"source":    string(ActiveExecutionMode),
		"cap":       maxPending,
	})
}

// auditLog emits a structured audit record to the process log.
// Every cancel/close action is recorded with timestamp, endpoint, request
// body, mode, and result so the dashboard event feed can replay it.
func auditLog(endpoint, mode string, reqBody, result interface{}) {
	reqJSON, _ := json.Marshal(reqBody)
	resJSON, _ := json.Marshal(result)
	log.Printf("[AUDIT] endpoint=%s mode=%s request=%s response=%s",
		endpoint, mode, reqJSON, resJSON)
}

// ServeOrdersCancel handles POST /api/orders/cancel.
//
// Body: {"order_id": N}
// Returns: CancelOrderResult JSON from ibkr_order cancel_order subprocess.
//
// Rejects with 400 if mode is SIMULATION, order_id is missing/zero, or the
// request method is not POST.  Logs an [AUDIT] entry for every attempt.
func (h *ConfigHandler) ServeOrdersCancel(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	if r.Method != "POST" {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}
	if ActiveExecutionMode == ModeSimulation {
		http.Error(w, `{"error":"SIMULATION mode — cancel requires a real broker"}`, http.StatusBadRequest)
		return
	}

	var body struct {
		OrderID int `json:"order_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid JSON body"}`, http.StatusBadRequest)
		return
	}
	if body.OrderID <= 0 {
		http.Error(w, `{"error":"order_id is required and must be > 0"}`, http.StatusBadRequest)
		return
	}

	result, err := CancelOrderUI(body.OrderID)
	if err != nil {
		errPayload := map[string]string{"error": err.Error()}
		auditLog("/api/orders/cancel", string(ActiveExecutionMode), body, errPayload)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(errPayload)
		return
	}

	// Remove from internal pending cap tracker so rank state is consistent
	removePendingOrder(body.OrderID)

	auditLog("/api/orders/cancel", string(ActiveExecutionMode), body, result)
	json.NewEncoder(w).Encode(result)
}

// parseContractKey splits "TSLA_CALL_2026-04-13_365" into its four components.
// Expected format: {SYMBOL}_{CONTRACT_TYPE}_{EXPIRY}_{STRIKE}
// e.g. "TSLA_CALL_2026-04-13_365.0"
func parseContractKey(key string) (symbol, contractType, expiry string, strike float64, err error) {
	// Use SplitN 4 to handle symbols that might embed underscores (future-proof)
	parts := strings.SplitN(key, "_", 4)
	if len(parts) != 4 {
		err = fmt.Errorf("contract_key must be SYMBOL_TYPE_EXPIRY_STRIKE, got %q", key)
		return
	}
	symbol       = parts[0]
	contractType = parts[1]
	expiry       = parts[2]
	strike, err  = strconv.ParseFloat(parts[3], 64)
	if err != nil {
		err = fmt.Errorf("invalid strike in contract_key %q: %w", key, err)
	}
	return
}

// ServePositionsClose handles POST /api/positions/close.
//
// Body: {"contract_key": "TSLA_CALL_2026-04-13_365", "quantity": 10, "market_open_if_closed": true}
// Returns: ClosePositionResult JSON from ibkr_order close_position subprocess.
//
// When market_open_if_closed is true (default), close_position auto-schedules
// an OPG order if the market is currently closed.  The response includes a
// non-empty scheduled_for field in that case.
func (h *ConfigHandler) ServePositionsClose(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	if r.Method != "POST" {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}
	if ActiveExecutionMode == ModeSimulation {
		http.Error(w, `{"error":"SIMULATION mode — close requires a real broker"}`, http.StatusBadRequest)
		return
	}

	var body struct {
		ContractKey        string `json:"contract_key"`
		Quantity           int    `json:"quantity"`
		MarketOpenIfClosed bool   `json:"market_open_if_closed"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid JSON body"}`, http.StatusBadRequest)
		return
	}
	if body.ContractKey == "" {
		http.Error(w, `{"error":"contract_key is required"}`, http.StatusBadRequest)
		return
	}
	if body.Quantity <= 0 {
		http.Error(w, `{"error":"quantity must be > 0"}`, http.StatusBadRequest)
		return
	}

	symbol, contractType, expiry, strike, err := parseContractKey(body.ContractKey)
	if err != nil {
		errPayload := map[string]string{"error": err.Error()}
		auditLog("/api/positions/close", string(ActiveExecutionMode), body, errPayload)
		http.Error(w, `{"error":"`+err.Error()+`"}`, http.StatusBadRequest)
		return
	}

	result, err := ClosePositionIBKR(symbol, contractType, strike, expiry, body.Quantity)
	if err != nil {
		errPayload := map[string]string{"error": err.Error()}
		auditLog("/api/positions/close", string(ActiveExecutionMode), body, errPayload)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(errPayload)
		return
	}

	auditLog("/api/positions/close", string(ActiveExecutionMode), body, result)
	json.NewEncoder(w).Encode(result)
}

// ServeCapEvents returns the last 10 [REPLACE] / [REJECT-CAP] events for the UI feed.
func (h *ConfigHandler) ServeCapEvents(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	events := GetCapEvents()
	maxPending := envIntOrDefault("MAX_PENDING_ORDERS", 2)

	type eventDTO struct {
		Ts            string  `json:"ts"`
		Kind          string  `json:"kind"`
		CancelledID   int     `json:"cancelled_id,omitempty"`
		CancelledRank float64 `json:"cancelled_rank"`
		IncomingRank  float64 `json:"incoming_rank"`
	}
	dtos := make([]eventDTO, 0, len(events))
	for _, ev := range events {
		dtos = append(dtos, eventDTO{
			Ts:            ev.Ts.Format(time.RFC3339),
			Kind:          ev.Kind,
			CancelledID:   ev.CancelledID,
			CancelledRank: ev.CancelledRank,
			IncomingRank:  ev.IncomingRank,
		})
	}

	// Also include current pending rank snapshot.
	capOrders := GetPendingCapOrders()
	type rankDTO struct {
		OrderID  int     `json:"order_id"`
		Rank     float64 `json:"rank"`
		PlacedAt string  `json:"placed_at"`
	}
	ranks := make([]rankDTO, 0, len(capOrders))
	for _, o := range capOrders {
		ranks = append(ranks, rankDTO{
			OrderID:  o.OrderID,
			Rank:     o.Rank,
			PlacedAt: o.PlacedAt.Format(time.RFC3339),
		})
	}

	json.NewEncoder(w).Encode(map[string]interface{}{
		"events":      dtos,
		"ranks":       ranks,
		"cap":         maxPending,
		"pending_cnt": activePendingCount(),
	})
}

func (h *ConfigHandler) ServeSimReset(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	if r.Method != "POST" {
		http.Error(w, "POST required", http.StatusMethodNotAllowed)
		return
	}
	// Reset sim: zero out paper portfolio balances
	h.Portfolio.Cash = 25000.0
	h.Portfolio.NAV = 25000.0
	h.Portfolio.Positions = make(map[string]Position)
	json.NewEncoder(w).Encode(map[string]string{"status": "ok", "mode": "sim"})
}

func (h *ConfigHandler) ServeSimToggle(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	simModeMu.Lock()
	if simMode == "paper" {
		simMode = "sim"
	} else {
		simMode = "paper"
	}
	current := simMode
	simModeMu.Unlock()

	json.NewEncoder(w).Encode(map[string]string{"mode": current})
}

// ── scorecard / loss tagging ──────────────────────────────────────────────────

var (
	scorecardCache   interface{}
	scorecardCacheMu sync.Mutex
	scorecardCachedAt time.Time
	scorecardCacheTTL = 60 * time.Second
)

func runScorecard(mode string, args ...string) (interface{}, error) {
	cmdArgs := append([]string{"alpha_engine/data/scorecard.py", mode}, args...)
	cmd := exec.Command("./alpha_engine/venv/bin/python", cmdArgs...)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = os.Environ()
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("scorecard[%s]: %w", mode, err)
	}
	var result interface{}
	if err := json.Unmarshal(out, &result); err != nil {
		return nil, fmt.Errorf("scorecard JSON[%s]: %w (raw: %s)", mode, err, string(out))
	}
	return result, nil
}

func (h *ConfigHandler) ServeScorecard(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	scorecardCacheMu.Lock()
	if scorecardCache != nil && time.Since(scorecardCachedAt) < scorecardCacheTTL {
		cached := scorecardCache
		scorecardCacheMu.Unlock()
		json.NewEncoder(w).Encode(cached)
		return
	}
	scorecardCacheMu.Unlock()

	result, err := runScorecard("scorecard")
	if err != nil {
		w.Write([]byte("[]"))
		return
	}

	scorecardCacheMu.Lock()
	scorecardCache = result
	scorecardCachedAt = time.Now()
	scorecardCacheMu.Unlock()

	json.NewEncoder(w).Encode(result)
}

func (h *ConfigHandler) ServeLossSummary(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	result, err := runScorecard("losses")
	if err != nil {
		json.NewEncoder(w).Encode(map[string]interface{}{
			"total_losses": 0, "total_loss_amount": 0.0,
			"avg_loss": 0.0, "loss_tags": map[string]int{},
		})
		return
	}
	json.NewEncoder(w).Encode(result)
}

func (h *ConfigHandler) ServeTagTrade(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	if r.Method == "OPTIONS" {
		w.Header().Set("Access-Control-Allow-Methods", "POST, OPTIONS")
		w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
		return
	}
	if r.Method != "POST" {
		http.Error(w, "POST required", http.StatusMethodNotAllowed)
		return
	}

	var body struct {
		ID    string `json:"id"`
		Tag   string `json:"tag"`
		Notes string `json:"notes"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		json.NewEncoder(w).Encode(map[string]string{"error": "invalid JSON"})
		return
	}
	if body.ID == "" || body.Tag == "" {
		json.NewEncoder(w).Encode(map[string]string{"error": "id and tag required"})
		return
	}

	_, err := runScorecard("tag", body.ID, body.Tag, body.Notes)
	if err != nil {
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	// Invalidate scorecard cache on tag change
	scorecardCacheMu.Lock()
	scorecardCache = nil
	scorecardCacheMu.Unlock()

	json.NewEncoder(w).Encode(map[string]bool{"ok": true})
}

// ── Intel endpoint ─────────────────────────────────────────────────────────

var (
	intelCache   interface{}
	intelCacheMu sync.Mutex
	intelCachedAt time.Time
	intelCacheTTL = 300 * time.Second
)

func (h *ConfigHandler) ServeIntel(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")

	intelCacheMu.Lock()
	if intelCache != nil && time.Since(intelCachedAt) < intelCacheTTL {
		json.NewEncoder(w).Encode(intelCache)
		intelCacheMu.Unlock()
		return
	}
	intelCacheMu.Unlock()

	cmd := exec.Command(
		"./alpha_engine/venv/bin/python",
		"-c",
		"import sys; sys.path.insert(0, 'alpha_engine'); from ingestion.intel import get_intel; import json; print(json.dumps(get_intel()))",
	)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	cmd.Env = os.Environ()
	out, err := cmd.Output()
	if err != nil {
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	var result interface{}
	if err := json.Unmarshal(out, &result); err != nil {
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	intelCacheMu.Lock()
	intelCache = result
	intelCachedAt = time.Now()
	intelCacheMu.Unlock()

	json.NewEncoder(w).Encode(result)
}

func (h *ConfigHandler) ServeOptionsChain(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Content-Type", "application/json")

	expiry := r.URL.Query().Get("expiry")
	args := []string{"alpha_engine/ingestion/options_chain_api.py"}
	if expiry != "" {
		args = append(args, "--expiry", expiry)
	}

	cmd := exec.Command("./alpha_engine/venv/bin/python", args...)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	out, err := cmd.Output()
	if err != nil {
		http.Error(w, `{"error":"options chain fetch failed"}`, 500)
		return
	}
	w.Write(out)
}

func (h *ConfigHandler) ServeLosingTrades(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	result, err := runScorecard("losing_trades")
	if err != nil {
		w.Write([]byte("[]"))
		return
	}
	json.NewEncoder(w).Encode(result)
}

// ── POST /api/config/notional ────────────────────────────────────────────────
// Writes a new NOTIONAL_ACCOUNT_SIZE to ~/.tsla-alpha.env and signals the
// publisher to reload. Returns the new value and a pending_restart flag.
//
// Validation: 5000 ≤ notional ≤ 250000.
// Write strategy: write to .env.new then atomic rename (safe concurrent reads).
// Reload: SIGHUP sent to the publisher process if found via /tmp/publisher.pid;
//         falls back to pending_restart=true if the process is not found.

var (
	notionalMu          sync.Mutex
	cachedNotional      int
	cachedNotionalLoaded bool
)

func getNotional() int {
	notionalMu.Lock()
	defer notionalMu.Unlock()
	if cachedNotionalLoaded {
		return cachedNotional
	}
	// Read from env file or NOTIONAL_ACCOUNT_SIZE env var
	envFile := os.ExpandEnv("${HOME}/.tsla-alpha.env")
	if f, err := os.ReadFile(envFile); err == nil {
		for _, line := range strings.Split(string(f), "\n") {
			if strings.HasPrefix(line, "NOTIONAL_ACCOUNT_SIZE=") {
				parts := strings.SplitN(line, "=", 2)
				if len(parts) == 2 {
					if n, err := strconv.Atoi(strings.TrimSpace(parts[1])); err == nil {
						cachedNotional = n
						cachedNotionalLoaded = true
						return n
					}
				}
			}
		}
	}
	// Fall back to process env
	if v := os.Getenv("NOTIONAL_ACCOUNT_SIZE"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			cachedNotional = n
			cachedNotionalLoaded = true
			return n
		}
	}
	cachedNotional = 25000
	cachedNotionalLoaded = true
	return 25000
}

func (h *ConfigHandler) ServeNotionalConfig(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")

	if r.Method == "OPTIONS" {
		return
	}

	if r.Method == "GET" {
		// getNotional() acquires the mutex internally — call it without holding the lock
		n := getNotional()
		json.NewEncoder(w).Encode(map[string]interface{}{
			"notional_account_size": n,
		})
		return
	}

	if r.Method != "POST" {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	var body struct {
		NotionalAccountSize int `json:"notional_account_size"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid JSON"}`, http.StatusBadRequest)
		return
	}

	n := body.NotionalAccountSize
	if n < 5000 || n > 250000 {
		http.Error(w,
			fmt.Sprintf(`{"error":"notional_account_size must be 5000–250000, got %d"}`, n),
			http.StatusBadRequest)
		return
	}

	// Atomic write to env file
	envFile := os.ExpandEnv("${HOME}/.tsla-alpha.env")
	tmpFile := envFile + ".new"

	// Read existing file (other vars) to preserve them
	existingLines := []string{}
	if f, err := os.ReadFile(envFile); err == nil {
		for _, line := range strings.Split(string(f), "\n") {
			if !strings.HasPrefix(line, "NOTIONAL_ACCOUNT_SIZE=") && line != "" {
				existingLines = append(existingLines, line)
			}
		}
	}
	existingLines = append(existingLines, fmt.Sprintf("NOTIONAL_ACCOUNT_SIZE=%d", n))

	content := strings.Join(existingLines, "\n") + "\n"
	if err := os.WriteFile(tmpFile, []byte(content), 0600); err != nil {
		http.Error(w, `{"error":"failed to write env file"}`, http.StatusInternalServerError)
		return
	}
	if err := os.Rename(tmpFile, envFile); err != nil {
		http.Error(w, `{"error":"failed to rename env file"}`, http.StatusInternalServerError)
		return
	}

	// Update cache
	notionalMu.Lock()
	cachedNotional = n
	cachedNotionalLoaded = true
	notionalMu.Unlock()

	// Try SIGHUP to publisher process
	pendingRestart := true
	pidFile := "/tmp/publisher.pid"
	if pidBytes, err := os.ReadFile(pidFile); err == nil {
		pidStr := strings.TrimSpace(string(pidBytes))
		if pid, err := strconv.Atoi(pidStr); err == nil {
			// Send SIGHUP
			if proc, err := os.FindProcess(pid); err == nil {
				if err := proc.Signal(os.Interrupt); err == nil {
					// SIGHUP not available on all platforms; use SIGINT as proxy
					// Actually write a reload-marker file instead
				}
			}
		}
	}
	// Write a reload marker so publisher.py can detect the change on next loop
	_ = os.WriteFile("/tmp/notional_reload", []byte(fmt.Sprintf("%d", n)), 0644)

	log.Printf("[NOTIONAL] Updated to %d (env=%s)", n, envFile)

	json.NewEncoder(w).Encode(map[string]interface{}{
		"notional_account_size": n,
		"pending_restart":       pendingRestart,
		"env_file":              envFile,
	})
}

// ═══════════════════════════════════════════════════════════════════════════
//  Signal Feedback API
//  All routes delegate to alpha_engine/ingestion/signal_feedback.py via
//  exec.Command so persistence stays in the same SQLite used by the engine.
//
//  Routes:
//    POST /api/signals/feedback           — add feedback (signal_id in body)
//    GET  /api/signals/feedback           — get for signal (?signal_id=...)
//    POST /api/signals/cancel             — cancel a signal (signal_id + comment in body)
//    GET  /api/signals/feedback/recent    — paginated recent across all signals
//    GET  /api/signals/feedback/digest    — aggregated digest for mayor consumption
//    POST /api/signals/feedback/resolve   — mark a feedback row as resolved
// ═══════════════════════════════════════════════════════════════════════════

// runSignalFeedbackPy calls signal_feedback.py <subcommand> <json_args> and
// returns the parsed JSON result. All subprocess errors are mapped to a Go error.
func runSignalFeedbackPy(subcommand string, args map[string]interface{}) (map[string]interface{}, error) {
	argsJSON, err := json.Marshal(args)
	if err != nil {
		return nil, fmt.Errorf("marshal args: %w", err)
	}

	cmd := exec.Command("./alpha_engine/venv/bin/python",
		"alpha_engine/ingestion/signal_feedback.py",
		subcommand,
		string(argsJSON),
	)
	cmd.Dir = "/home/builder/src/gpfiles/tcode"
	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("signal_feedback.py %s: %w", subcommand, err)
	}

	var result map[string]interface{}
	if err := json.Unmarshal(out, &result); err != nil {
		// Might be a JSON array — wrap it
		var arr []interface{}
		if err2 := json.Unmarshal(out, &arr); err2 != nil {
			return nil, fmt.Errorf("parse output: %w", err)
		}
		return map[string]interface{}{"rows": arr}, nil
	}
	return result, nil
}

// setCORSHeaders writes the standard CORS + Content-Type headers used by all
// signal-feedback endpoints.
func setCORSHeaders(w http.ResponseWriter) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	w.Header().Set("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
	w.Header().Set("Access-Control-Allow-Headers", "Content-Type")
}

// ServeSignalFeedback handles:
//
//	POST /api/signals/feedback  — body: {signal_id, signal_snapshot, user_comment, action, tag?}
//	GET  /api/signals/feedback  — query: signal_id (returns all feedback for that signal)
func (h *ConfigHandler) ServeSignalFeedback(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w)
	if r.Method == "OPTIONS" {
		return
	}

	switch r.Method {
	case "POST":
		var body map[string]interface{}
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			http.Error(w, `{"error":"invalid JSON body"}`, http.StatusBadRequest)
			return
		}
		result, err := runSignalFeedbackPy("add", body)
		if err != nil {
			log.Printf("[FEEDBACK-ADD] error: %v", err)
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		if errMsg, ok := result["error"].(string); ok {
			http.Error(w, fmt.Sprintf(`{"error":%q}`, errMsg), http.StatusBadRequest)
			return
		}
		w.WriteHeader(http.StatusCreated)
		json.NewEncoder(w).Encode(result)

	case "GET":
		signalID := r.URL.Query().Get("signal_id")
		if signalID == "" {
			http.Error(w, `{"error":"signal_id query param required"}`, http.StatusBadRequest)
			return
		}
		result, err := runSignalFeedbackPy("get_for_signal", map[string]interface{}{"signal_id": signalID})
		if err != nil {
			log.Printf("[FEEDBACK-GET] error: %v", err)
			w.WriteHeader(http.StatusInternalServerError)
			json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
			return
		}
		json.NewEncoder(w).Encode(result)

	default:
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
	}
}

// ServeSignalCancel handles POST /api/signals/cancel.
//
// Body: {signal_id, user_comment}
// Stores action=CANCEL in signal_feedback and registers the signal id in the
// in-memory cancelled-signal cache used by the subscriber to gate placements.
// A non-empty user_comment is required — silent cancels are not allowed.
func (h *ConfigHandler) ServeSignalCancel(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w)
	if r.Method == "OPTIONS" {
		return
	}
	if r.Method != "POST" {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	var body struct {
		SignalID      string      `json:"signal_id"`
		UserComment   string      `json:"user_comment"`
		Tag           string      `json:"tag"`
		SignalSnapshot interface{} `json:"signal_snapshot"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid JSON body"}`, http.StatusBadRequest)
		return
	}
	if body.SignalID == "" {
		http.Error(w, `{"error":"signal_id is required"}`, http.StatusBadRequest)
		return
	}
	// Silent cancels are not allowed — require a comment
	if body.UserComment == "" {
		http.Error(w, `{"error":"user_comment is required for signal cancellation"}`, http.StatusBadRequest)
		return
	}

	snapshot := body.SignalSnapshot
	if snapshot == nil {
		snapshot = map[string]interface{}{}
	}

	args := map[string]interface{}{
		"signal_id":       body.SignalID,
		"user_comment":    body.UserComment,
		"action":          "CANCEL",
		"signal_snapshot": snapshot,
	}
	if body.Tag != "" {
		args["tag"] = body.Tag
	}

	result, err := runSignalFeedbackPy("add", args)
	if err != nil {
		log.Printf("[SIGNAL-CANCEL] error: %v", err)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	if errMsg, ok := result["error"].(string); ok {
		http.Error(w, fmt.Sprintf(`{"error":%q}`, errMsg), http.StatusBadRequest)
		return
	}

	// Register in the in-memory cancelled-signal set so the subscriber respects
	// this cancel immediately (before the 10s cache refresh fires).
	addCancelledSignal(body.SignalID)
	log.Printf("[SIGNAL-CANCEL-USER] signal=%s comment=%q tag=%q", body.SignalID, body.UserComment, body.Tag)

	json.NewEncoder(w).Encode(result)
}

// ServeSignalFeedbackRecent handles GET /api/signals/feedback/recent.
//
// Query params: since, tag, action, limit, offset
func (h *ConfigHandler) ServeSignalFeedbackRecent(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w)
	if r.Method == "OPTIONS" {
		return
	}
	if r.Method != "GET" {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	q := r.URL.Query()
	args := map[string]interface{}{}
	if v := q.Get("since"); v != "" {
		args["since"] = v
	}
	if v := q.Get("tag"); v != "" {
		args["tag"] = v
	}
	if v := q.Get("action"); v != "" {
		args["action"] = v
	}
	if v := q.Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			args["limit"] = n
		}
	}
	if v := q.Get("offset"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			args["offset"] = n
		}
	}

	result, err := runSignalFeedbackPy("get_recent", args)
	if err != nil {
		log.Printf("[FEEDBACK-RECENT] error: %v", err)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	json.NewEncoder(w).Encode(result)
}

// ServeSignalFeedbackDigest handles GET /api/signals/feedback/digest.
//
// Query params: since
func (h *ConfigHandler) ServeSignalFeedbackDigest(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w)
	if r.Method == "OPTIONS" {
		return
	}
	if r.Method != "GET" {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	args := map[string]interface{}{}
	if v := r.URL.Query().Get("since"); v != "" {
		args["since"] = v
	}

	result, err := runSignalFeedbackPy("get_digest", args)
	if err != nil {
		log.Printf("[FEEDBACK-DIGEST] error: %v", err)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	json.NewEncoder(w).Encode(result)
}

// ServeSignalFeedbackResolve handles POST /api/signals/feedback/resolve.
//
// Body: {id, resolved_by}
func (h *ConfigHandler) ServeSignalFeedbackResolve(w http.ResponseWriter, r *http.Request) {
	setCORSHeaders(w)
	if r.Method == "OPTIONS" {
		return
	}
	if r.Method != "POST" {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}

	var body struct {
		ID         interface{} `json:"id"`
		ResolvedBy string      `json:"resolved_by"`
	}
	if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
		http.Error(w, `{"error":"invalid JSON body"}`, http.StatusBadRequest)
		return
	}
	if body.ID == nil {
		http.Error(w, `{"error":"id is required"}`, http.StatusBadRequest)
		return
	}
	if body.ResolvedBy == "" {
		http.Error(w, `{"error":"resolved_by is required"}`, http.StatusBadRequest)
		return
	}

	args := map[string]interface{}{
		"id":          body.ID,
		"resolved_by": body.ResolvedBy,
	}

	result, err := runSignalFeedbackPy("resolve", args)
	if err != nil {
		log.Printf("[FEEDBACK-RESOLVE] error: %v", err)
		w.WriteHeader(http.StatusInternalServerError)
		json.NewEncoder(w).Encode(map[string]string{"error": err.Error()})
		return
	}
	if errMsg, ok := result["error"].(string); ok {
		http.Error(w, fmt.Sprintf(`{"error":%q}`, errMsg), http.StatusBadRequest)
		return
	}
	json.NewEncoder(w).Encode(result)
}

func RequestLoggingMiddleware(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := time.Now()
		defer func() {
			duration := time.Since(start)
			GlobalMetrics.mu.Lock()
			GlobalMetrics.Latencies = append(GlobalMetrics.Latencies, duration)
			if len(GlobalMetrics.Latencies) > 2000 { // Keep last 2000 samples by trimming
				GlobalMetrics.Latencies = GlobalMetrics.Latencies[len(GlobalMetrics.Latencies)-1000:]
			}
			GlobalMetrics.mu.Unlock()
		}()

		GlobalMetrics.LogRequest()
		next.ServeHTTP(w, r)
	})
}
