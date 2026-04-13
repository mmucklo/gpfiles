package main

import (
	"encoding/json"
	"fmt"
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
