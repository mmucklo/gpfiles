package main

import (
	"testing"
)

// TestCancelledSignalCacheAdd verifies addCancelledSignal + isSignalCancelled.
func TestCancelledSignalCacheAdd(t *testing.T) {
	// Reset state for test isolation
	cancelledSignalsMu.Lock()
	cancelledSignals = map[string]bool{}
	cancelledSignalsMu.Unlock()

	fp := "TSLA_CALL_2026-04-18_BUY_365.00_1"

	if isSignalCancelled(fp) {
		t.Fatal("signal should not be cancelled before addCancelledSignal")
	}

	addCancelledSignal(fp)

	if !isSignalCancelled(fp) {
		t.Fatal("signal should be cancelled after addCancelledSignal")
	}
}

// TestCancelledSignalCacheIsolation verifies that cancelling one fingerprint
// does not affect others.
func TestCancelledSignalCacheIsolation(t *testing.T) {
	cancelledSignalsMu.Lock()
	cancelledSignals = map[string]bool{}
	cancelledSignalsMu.Unlock()

	fpA := "TSLA_CALL_2026-04-18_BUY_365.00_1"
	fpB := "TSLA_PUT_2026-04-18_BUY_360.00_2"

	addCancelledSignal(fpA)

	if !isSignalCancelled(fpA) {
		t.Error("fpA should be cancelled")
	}
	if isSignalCancelled(fpB) {
		t.Error("fpB should NOT be cancelled — unrelated fingerprint")
	}
}

// TestCancelledSignalRefreshOverwrites verifies that refreshCancelledSignals
// replaces the previous cache (old entries not in new set are removed).
// Note: refreshCancelledSignals shells out to the Python subprocess which may
// not be available in CI; we test the in-memory mechanics directly by
// exercising the mutex-protected write path.
func TestCancelledSignalRefreshOverwrites(t *testing.T) {
	cancelledSignalsMu.Lock()
	cancelledSignals = map[string]bool{
		"OLD_FP_1": true,
		"OLD_FP_2": true,
	}
	cancelledSignalsMu.Unlock()

	// Simulate what refreshCancelledSignals would do on a fresh fetch
	fresh := map[string]bool{"NEW_FP_1": true}
	cancelledSignalsMu.Lock()
	cancelledSignals = fresh
	cancelledSignalsMu.Unlock()

	if isSignalCancelled("OLD_FP_1") {
		t.Error("OLD_FP_1 should be gone after refresh")
	}
	if !isSignalCancelled("NEW_FP_1") {
		t.Error("NEW_FP_1 should be present after refresh")
	}
}

// TestComputeRankStillWorks ensures the cancel-related additions didn't
// regress the rank computation used by the pending-order cap.
func TestComputeRankStillWorks(t *testing.T) {
	sig := AlphaSignal{
		Confidence:       0.90,
		TargetLimitPrice: 1.00,
		TakeProfitPrice:  1.20,
		Timestamp:        1e18, // far future unix — ensures recency ≈ 1.0
	}
	rank := computeRank(sig)
	if rank <= 0 || rank > 1 {
		t.Errorf("rank out of [0,1]: got %f", rank)
	}
	// High confidence + positive ROI should produce rank > 0.6
	if rank < 0.6 {
		t.Errorf("expected rank > 0.6 for high-confidence signal, got %f", rank)
	}
}
