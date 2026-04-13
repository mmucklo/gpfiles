package main

import (
	"math"
	"testing"
	"time"
)

// ── computeRank tests ─────────────────────────────────────────────────────────

func TestRankHighConfidenceHighROI(t *testing.T) {
	sig := AlphaSignal{
		Confidence:       0.95,
		Timestamp:        float64(time.Now().Unix()), // fresh
		TargetLimitPrice: 1.0,
		TakeProfitPrice:  2.0, // 100% ROI
	}
	rank := computeRank(sig)
	if rank < 0.7 {
		t.Errorf("high confidence + high ROI fresh signal should rank > 0.7, got %.3f", rank)
	}
	if rank > 1.0 {
		t.Errorf("rank must be capped at 1.0, got %.3f", rank)
	}
}

func TestRankLowConfidenceLowROI(t *testing.T) {
	sig := AlphaSignal{
		Confidence:       0.2,
		Timestamp:        float64(time.Now().Unix()),
		TargetLimitPrice: 1.0,
		TakeProfitPrice:  1.05, // 5% ROI
	}
	rank := computeRank(sig)
	if rank > 0.4 {
		t.Errorf("low confidence + low ROI signal should rank < 0.4, got %.3f", rank)
	}
}

func TestRankStaleSignalPenalised(t *testing.T) {
	fresh := AlphaSignal{
		Confidence:       0.8,
		Timestamp:        float64(time.Now().Unix()),
		TargetLimitPrice: 1.0,
		TakeProfitPrice:  1.5,
	}
	// 45-minute-old signal
	stale := AlphaSignal{
		Confidence:       0.8,
		Timestamp:        float64(time.Now().Add(-45 * time.Minute).Unix()),
		TargetLimitPrice: 1.0,
		TakeProfitPrice:  1.5,
	}
	freshRank := computeRank(fresh)
	staleRank := computeRank(stale)
	if staleRank >= freshRank {
		t.Errorf("stale signal (%.3f) should rank lower than fresh (%.3f)", staleRank, freshRank)
	}
}

func TestRankClampedToUnitInterval(t *testing.T) {
	// Zero fields — should not panic or return outside [0,1].
	sig := AlphaSignal{Confidence: 0}
	rank := computeRank(sig)
	if rank < 0 || rank > 1 {
		t.Errorf("rank must be in [0,1], got %.3f", rank)
	}

	// Confidence > 1 (shouldn't happen, but guard against it).
	sig2 := AlphaSignal{Confidence: 5.0, Timestamp: float64(time.Now().Unix())}
	rank2 := computeRank(sig2)
	if rank2 > 1 {
		t.Errorf("rank must be capped at 1.0 even with over-range confidence, got %.3f", rank2)
	}
}

func TestRankROICapAt100Pct(t *testing.T) {
	// ROI 1000% should not inflate rank above what ROI=100% gives.
	base := AlphaSignal{
		Confidence:       0.5,
		Timestamp:        float64(time.Now().Unix()),
		TargetLimitPrice: 1.0,
		TakeProfitPrice:  2.0, // 100% ROI — hits cap
	}
	extreme := AlphaSignal{
		Confidence:       0.5,
		Timestamp:        float64(time.Now().Unix()),
		TargetLimitPrice: 1.0,
		TakeProfitPrice:  100.0, // 9900% ROI — beyond cap
	}
	diff := math.Abs(computeRank(base) - computeRank(extreme))
	if diff > 0.001 {
		t.Errorf("ROI beyond 100%% should be capped; base=%.3f extreme=%.3f diff=%.4f",
			computeRank(base), computeRank(extreme), diff)
	}
}

// ── Pending cap helper tests ──────────────────────────────────────────────────

func resetPendingCapState() {
	pendingCapMu.Lock()
	pendingCapOrders = map[int]pendingOrderInfo{}
	pendingCapMu.Unlock()
	capEventsMu.Lock()
	capEvents = nil
	capEventsMu.Unlock()
}

func TestActivePendingCountEmpty(t *testing.T) {
	resetPendingCapState()
	if n := activePendingCount(); n != 0 {
		t.Errorf("expected 0 pending, got %d", n)
	}
}

func TestAddAndRemovePendingOrder(t *testing.T) {
	resetPendingCapState()
	sig := AlphaSignal{Confidence: 0.8}
	addPendingOrder(101, 0.75, sig)
	addPendingOrder(102, 0.60, sig)
	if n := activePendingCount(); n != 2 {
		t.Fatalf("expected 2 pending, got %d", n)
	}
	removePendingOrder(101)
	if n := activePendingCount(); n != 1 {
		t.Errorf("expected 1 pending after remove, got %d", n)
	}
}

func TestLowestRankedPending(t *testing.T) {
	resetPendingCapState()
	sig := AlphaSignal{Confidence: 0.5}
	addPendingOrder(10, 0.90, sig)
	addPendingOrder(11, 0.45, sig)
	addPendingOrder(12, 0.72, sig)

	lowest, found := lowestRankedPending()
	if !found {
		t.Fatal("expected to find lowest ranked pending order")
	}
	if lowest.OrderID != 11 {
		t.Errorf("expected orderId=11 (rank 0.45), got orderId=%d rank=%.3f", lowest.OrderID, lowest.Rank)
	}
}

func TestCapEventRecording(t *testing.T) {
	capEventsMu.Lock()
	capEvents = nil
	capEventsMu.Unlock()

	recordCapEvent(capReplacementEvent{Ts: time.Now(), Kind: "REPLACE", CancelledID: 5, CancelledRank: 0.4, IncomingRank: 0.8})
	recordCapEvent(capReplacementEvent{Ts: time.Now(), Kind: "REJECT-CAP", IncomingRank: 0.3, CancelledRank: 0.5})

	evs := GetCapEvents()
	if len(evs) != 2 {
		t.Fatalf("expected 2 cap events, got %d", len(evs))
	}
	// Most recent event should be first.
	if evs[0].Kind != "REJECT-CAP" {
		t.Errorf("expected most recent event first (REJECT-CAP), got %s", evs[0].Kind)
	}
}

func TestCapEventRingBuffer(t *testing.T) {
	capEventsMu.Lock()
	capEvents = nil
	capEventsMu.Unlock()

	// Add 15 events — ring buffer should cap at 10.
	for i := 0; i < 15; i++ {
		recordCapEvent(capReplacementEvent{Ts: time.Now(), Kind: "REPLACE"})
	}
	evs := GetCapEvents()
	if len(evs) != 10 {
		t.Errorf("expected ring buffer to hold exactly 10 events, got %d", len(evs))
	}
}
