package main

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"os/exec"
)

// OptionContract identifies the option contract for order placement.
type OptionContract struct {
	Symbol     string
	OptionType string  // "CALL" or "PUT"
	Strike     float64
	Expiry     string  // YYYY-MM-DD
}

// OrderResult is the JSON payload returned by ingestion/ibkr_order.py.
type OrderResult struct {
	OrderID      int     `json:"orderId"`
	Status       string  `json:"status"`
	FilledQty    float64 `json:"filled_qty"`
	AvgFillPrice float64 `json:"avg_fill_price"`
	Timestamp    string  `json:"timestamp"`
	ContractID   int     `json:"contract_id"`
	Error        string  `json:"error,omitempty"`
	// Contract fields — populated by the open_orders command.
	Symbol     string  `json:"symbol,omitempty"`
	Action     string  `json:"action,omitempty"`
	Qty        int     `json:"qty,omitempty"`
	Strike     float64 `json:"strike,omitempty"`
	Expiry     string  `json:"expiry,omitempty"`
	OptionType string  `json:"option_type,omitempty"`
	LimitPrice float64 `json:"limit_price,omitempty"`
}

// pythonBin returns the path to the venv Python interpreter.
// Callers set cmd.Dir = "./alpha_engine" so the relative path resolves correctly.
func pythonBin() string {
	if bin := os.Getenv("PYTHON_BIN"); bin != "" {
		return bin
	}
	return "./venv/bin/python"
}

// stderrString extracts stderr from an *exec.ExitError, used for diagnostics.
func stderrString(err error) string {
	if exitErr, ok := err.(*exec.ExitError); ok {
		return string(exitErr.Stderr)
	}
	return ""
}

// PlaceIBKROrder shells out to ingestion/ibkr_order.py to place an options limit
// order via the real IB Gateway.  The Python subprocess opens its own TCP
// connection (ib.connect) and closes it after the order is submitted.
//
// This is the only legitimate path for IBKR_PAPER mode order placement.
// There is no stub, no fake print, no internal simulation fallback.
func PlaceIBKROrder(contract OptionContract, action string, qty int, limitPrice float64) (*OrderResult, error) {
	mode := string(ActiveExecutionMode)
	clientID := AllocateClientID()

	args := []string{
		"-m", "ingestion.ibkr_order", "place",
		"--symbol", contract.Symbol,
		"--contract", contract.OptionType,
		"--strike", fmt.Sprintf("%g", contract.Strike),
		"--expiry", contract.Expiry,
		"--action", action,
		"--quantity", fmt.Sprintf("%d", qty),
		"--limit-price", fmt.Sprintf("%g", limitPrice),
		"--mode", mode,
		"--client-id", fmt.Sprintf("%d", clientID),
	}

	cmd := exec.Command(pythonBin(), args...)
	cmd.Dir = "./alpha_engine"
	cmd.Env = os.Environ()

	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("ibkr_order subprocess failed: %w (stderr=%s)", err, stderrString(err))
	}

	var result OrderResult
	if err := json.Unmarshal(out, &result); err != nil {
		return nil, fmt.Errorf("ibkr_order JSON parse: %w (raw=%s)", err, string(out))
	}
	if result.Error != "" {
		return nil, fmt.Errorf("ibkr_order error: %s", result.Error)
	}
	return &result, nil
}

// CancelIBKROrder cancels an open IBKR order by order ID.
func CancelIBKROrder(orderID int) error {
	clientID := AllocateClientID()
	args := []string{
		"-m", "ingestion.ibkr_order", "cancel",
		"--order-id", fmt.Sprintf("%d", orderID),
		"--mode", string(ActiveExecutionMode),
		"--client-id", fmt.Sprintf("%d", clientID),
	}

	cmd := exec.Command(pythonBin(), args...)
	cmd.Dir = "./alpha_engine"
	cmd.Env = os.Environ()

	out, err := cmd.Output()
	if err != nil {
		return fmt.Errorf("ibkr_order cancel failed: %w (stderr=%s)", err, stderrString(err))
	}

	var result map[string]interface{}
	if err := json.Unmarshal(out, &result); err != nil {
		return fmt.Errorf("ibkr_order cancel JSON: %w", err)
	}
	if errMsg, ok := result["error"].(string); ok && errMsg != "" {
		return fmt.Errorf("ibkr_order cancel: %s", errMsg)
	}
	return nil
}

// BracketOrderResult is the JSON payload returned by ibkr_order.py for a bracket placement.
type BracketOrderResult struct {
	ParentOrderID     int    `json:"parent_order_id"`
	TakeProfitOrderID int    `json:"take_profit_order_id"`
	StopLossOrderID   int    `json:"stop_loss_order_id"`
	GroupOCA          string `json:"group_oca"`
	Status            string `json:"status"`
	Timestamp         string `json:"timestamp"`
	Error             string `json:"error,omitempty"`
}

// PlaceBracketIBKROrder shells out to ibkr_order.py to place a bracket order
// (parent LIMIT + TP LMT + SL STP LMT in an OCO group).
//
// When sig.StopLossUnderlyingPrice > 0 it passes --underlying-stop to condition
// the SL leg on the underlying stock price instead of option premium.
// If the underlying stop is 0, a 3% below-spot derived value is used.
//
// ANTI-PATTERN: never call PlaceIBKROrder as fallback if this returns an error.
// A failed bracket = rejected signal, full stop.
func PlaceBracketIBKROrder(contract OptionContract, sig AlphaSignal, action string, qty int, limitPrice float64) (*BracketOrderResult, error) {
	mode     := string(ActiveExecutionMode)
	clientID := AllocateClientID()

	args := []string{
		"-m", "ingestion.ibkr_order", "place",
		"--symbol",      contract.Symbol,
		"--contract",    contract.OptionType,
		"--strike",      fmt.Sprintf("%g", contract.Strike),
		"--expiry",      contract.Expiry,
		"--action",      action,
		"--quantity",    fmt.Sprintf("%d", qty),
		"--limit-price", fmt.Sprintf("%g", limitPrice),
		"--take-profit", fmt.Sprintf("%g", sig.TakeProfitPrice),
		"--stop-loss",   fmt.Sprintf("%g", sig.StopLossPrice),
		"--mode",        mode,
		"--client-id",   fmt.Sprintf("%d", clientID),
	}

	// Determine underlying stop price: explicit field or 3%-below-spot derivation
	underlyingStop := sig.StopLossUnderlyingPrice
	if underlyingStop <= 0 && sig.UnderlyingPrice > 0 {
		underlyingStop = sig.UnderlyingPrice * 0.97
		log.Printf("[BRACKET] Derived underlying stop %.2f (spot=%.2f × 0.97)", underlyingStop, sig.UnderlyingPrice)
	}
	if underlyingStop > 0 {
		args = append(args, "--underlying-stop",
			fmt.Sprintf("%s:%.4f", contract.Symbol, underlyingStop))
	}

	cmd := exec.Command(pythonBin(), args...)
	cmd.Dir = "./alpha_engine"
	cmd.Env = os.Environ()

	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("bracket ibkr_order subprocess failed: %w (stderr=%s)", err, stderrString(err))
	}

	var result BracketOrderResult
	if err := json.Unmarshal(out, &result); err != nil {
		return nil, fmt.Errorf("bracket ibkr_order JSON parse: %w (raw=%s)", err, string(out))
	}
	if result.Error != "" {
		return nil, fmt.Errorf("bracket ibkr_order error: %s", result.Error)
	}
	if result.ParentOrderID <= 0 || result.TakeProfitOrderID <= 0 || result.StopLossOrderID <= 0 {
		return nil, fmt.Errorf(
			"bracket returned zero orderId: parent=%d tp=%d sl=%d — rejecting to avoid unprotected leg",
			result.ParentOrderID, result.TakeProfitOrderID, result.StopLossOrderID,
		)
	}
	return &result, nil
}

// StartupGlobalCancel issues reqGlobalCancel() via ibkr_order.py to clear any
// orphan pre-Phase-9 naked orders at engine startup.
// Gated in main.go behind STARTUP_CLEAR_ORPHANS=1 (default 1).
func StartupGlobalCancel() (int, error) {
	clientID := AllocateClientID()
	args := []string{
		"-m", "ingestion.ibkr_order", "global_cancel",
		"--mode",      string(ActiveExecutionMode),
		"--client-id", fmt.Sprintf("%d", clientID),
	}

	cmd := exec.Command(pythonBin(), args...)
	cmd.Dir = "./alpha_engine"
	cmd.Env = os.Environ()

	out, err := cmd.Output()
	if err != nil {
		return 0, fmt.Errorf("global_cancel failed: %w (stderr=%s)", err, stderrString(err))
	}

	var result struct {
		OpenOrdersAfter int    `json:"open_orders_after"`
		Error           string `json:"error,omitempty"`
	}
	if err := json.Unmarshal(out, &result); err != nil {
		return 0, fmt.Errorf("global_cancel JSON: %w", err)
	}
	if result.Error != "" {
		return 0, fmt.Errorf("global_cancel: %s", result.Error)
	}
	return result.OpenOrdersAfter, nil
}

// ExpiryCloseIBKROrders market-sells all open option positions expiring on expiryDate.
// Called from the expiry-close scheduler goroutine between 15:25–15:35 ET.
func ExpiryCloseIBKROrders(expiryDate string) {
	clientID := AllocateClientID()
	args := []string{
		"-m", "ingestion.ibkr_order", "expiry_close",
		"--expiry-date", expiryDate,
		"--mode",        string(ActiveExecutionMode),
		"--client-id",   fmt.Sprintf("%d", clientID),
	}

	cmd := exec.Command(pythonBin(), args...)
	cmd.Dir = "./alpha_engine"
	cmd.Env = os.Environ()

	out, err := cmd.Output()
	if err != nil {
		log.Printf("[EXPIRY-CLOSE] subprocess failed: %v (stderr=%s)", err, stderrString(err))
		return
	}

	var result struct {
		ClosedCount int    `json:"closed_count"`
		OrderIDs    []int  `json:"order_ids"`
		Error       string `json:"error,omitempty"`
	}
	if err := json.Unmarshal(out, &result); err != nil {
		log.Printf("[EXPIRY-CLOSE] JSON parse failed: %v (raw=%s)", err, string(out))
		return
	}
	if result.Error != "" {
		log.Printf("[EXPIRY-CLOSE] error: %s", result.Error)
		return
	}
	for _, oid := range result.OrderIDs {
		log.Printf("[EXPIRY-CLOSE] orderId=%d", oid)
		removePendingOrder(oid)
	}
	log.Printf("[EXPIRY-CLOSE] closed %d expiring positions for %s", result.ClosedCount, expiryDate)
}

// CancelOrderResult is the JSON payload returned by ibkr_order.py cancel_order.
type CancelOrderResult struct {
	OrderID      int    `json:"order_id"`
	Status       string `json:"status"`
	OcaCancelled []int  `json:"oca_cancelled"` // sibling IDs cancelled via OCO
	Timestamp    string `json:"timestamp"`
	Error        string `json:"error,omitempty"`
}

// CancelOrderUI shells out to ibkr_order cancel_order — the UI-facing cancel path.
// Unlike the engine-internal CancelIBKROrder, it re-queries open_orders afterward
// to confirm bracket OCO siblings were also cancelled, and returns the verification result.
func CancelOrderUI(orderID int) (*CancelOrderResult, error) {
	clientID := AllocateClientID()
	args := []string{
		"-m", "ingestion.ibkr_order", "cancel_order",
		"--order-id", fmt.Sprintf("%d", orderID),
		"--mode",     string(ActiveExecutionMode),
		"--client-id", fmt.Sprintf("%d", clientID),
	}

	cmd := exec.Command(pythonBin(), args...)
	cmd.Dir = "./alpha_engine"
	cmd.Env = os.Environ()

	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("ibkr_order cancel_order failed: %w (stderr=%s)", err, stderrString(err))
	}

	var result CancelOrderResult
	if err := json.Unmarshal(out, &result); err != nil {
		return nil, fmt.Errorf("ibkr_order cancel_order JSON: %w (raw=%s)", err, string(out))
	}
	if result.Error != "" {
		return nil, fmt.Errorf("ibkr_order cancel_order: %s", result.Error)
	}
	return &result, nil
}

// ClosePositionResult is the JSON payload returned by ibkr_order.py close_position.
type ClosePositionResult struct {
	OrderID      int    `json:"order_id"`
	Status       string `json:"status"`
	ScheduledFor string `json:"scheduled_for"` // empty string → immediate; ISO8601 UTC → OPG-scheduled
	Timestamp    string `json:"timestamp"`
	Error        string `json:"error,omitempty"`
}

// ClosePositionIBKR shells out to ibkr_order close_position.
// The Python subprocess auto-detects market hours and either submits MKT DAY
// or schedules a TIF=OPG order for the next session open.
func ClosePositionIBKR(symbol, contractType string, strike float64, expiry string, qty int) (*ClosePositionResult, error) {
	clientID := AllocateClientID()
	args := []string{
		"-m", "ingestion.ibkr_order", "close_position",
		"--symbol",   symbol,
		"--contract", contractType,
		"--strike",   fmt.Sprintf("%g", strike),
		"--expiry",   expiry,
		"--quantity", fmt.Sprintf("%d", qty),
		"--mode",     string(ActiveExecutionMode),
		"--client-id", fmt.Sprintf("%d", clientID),
	}

	cmd := exec.Command(pythonBin(), args...)
	cmd.Dir = "./alpha_engine"
	cmd.Env = os.Environ()

	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("ibkr_order close_position failed: %w (stderr=%s)", err, stderrString(err))
	}

	var result ClosePositionResult
	if err := json.Unmarshal(out, &result); err != nil {
		return nil, fmt.Errorf("ibkr_order close_position JSON: %w (raw=%s)", err, string(out))
	}
	if result.Error != "" {
		return nil, fmt.Errorf("ibkr_order close_position: %s", result.Error)
	}
	return &result, nil
}

// OpenIBKROrders returns the list of currently open orders from IBKR.
// Used by the reconciler to sync internal state with the broker.
func OpenIBKROrders() ([]OrderResult, error) {
	clientID := AllocateClientID()
	args := []string{
		"-m", "ingestion.ibkr_order", "open_orders",
		"--mode", string(ActiveExecutionMode),
		"--client-id", fmt.Sprintf("%d", clientID),
	}

	cmd := exec.Command(pythonBin(), args...)
	cmd.Dir = "./alpha_engine"
	cmd.Env = os.Environ()

	out, err := cmd.Output()
	if err != nil {
		return nil, fmt.Errorf("ibkr_order open_orders failed: %w (stderr=%s)", err, stderrString(err))
	}

	var result struct {
		Orders []OrderResult `json:"orders"`
		Error  string        `json:"error,omitempty"`
	}
	if err := json.Unmarshal(out, &result); err != nil {
		return nil, fmt.Errorf("ibkr_order open_orders JSON: %w", err)
	}
	if result.Error != "" {
		return nil, fmt.Errorf("ibkr_order open_orders: %s", result.Error)
	}
	return result.Orders, nil
}
