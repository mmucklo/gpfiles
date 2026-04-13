package main

import (
	"fmt"
	"sync"
	"testing"
)

// stubPlaceCount counts how many times PlaceIBKROrder would be called.
// We simulate the dedup logic locally rather than spinning up a real broker.

// simulateSignalHandling mimics the dedup block in subscriber.go Start().
// It returns (placedCount, skipCount) after processing nSignals identical signals.
func simulateSignalHandling(
	fingerprints map[string]orderState,
	mu *sync.Mutex,
	fp string,
	fakeOrderID int,
	fakeStatus string,
	nSignals int,
) (placed int, skipped int) {
	for range nSignals {
		mu.Lock()
		prev, exists := fingerprints[fp]
		mu.Unlock()

		if exists && activeStatuses[prev.Status] {
			skipped++
			continue
		}

		// Simulate successful PlaceIBKROrder returning the same order each time.
		result := orderState{OrderID: fakeOrderID, Status: fakeStatus}
		mu.Lock()
		fingerprints[fp] = result
		mu.Unlock()

		placed++
	}
	return placed, skipped
}

func TestOrderDedupSameFingerprint(t *testing.T) {
	fingerprints := make(map[string]orderState)
	var mu sync.Mutex
	fp := signalFingerprint("TSLA", "CALL", "2026-04-18", "BUY", 365.0, 10)

	placed, skipped := simulateSignalHandling(fingerprints, &mu, fp, 42, "PreSubmitted", 5)

	if placed != 1 {
		t.Errorf("expected PlaceIBKROrder to be called once, got %d", placed)
	}
	if skipped != 4 {
		t.Errorf("expected 4 skipped signals, got %d", skipped)
	}
}

func TestOrderDedupDifferentFingerprints(t *testing.T) {
	fingerprints := make(map[string]orderState)
	var mu sync.Mutex

	// Two different strikes → two different fingerprints → both should place.
	fp1 := signalFingerprint("TSLA", "CALL", "2026-04-18", "BUY", 365.0, 10)
	fp2 := signalFingerprint("TSLA", "CALL", "2026-04-18", "BUY", 370.0, 10)

	p1, s1 := simulateSignalHandling(fingerprints, &mu, fp1, 1, "PreSubmitted", 3)
	p2, s2 := simulateSignalHandling(fingerprints, &mu, fp2, 2, "PreSubmitted", 3)

	if p1 != 1 || p2 != 1 {
		t.Errorf("each fingerprint should place exactly once; got p1=%d p2=%d", p1, p2)
	}
	if s1 != 2 || s2 != 2 {
		t.Errorf("expected 2 skips each; got s1=%d s2=%d", s1, s2)
	}
}

func TestOrderDedupCancelledAllowsReplacement(t *testing.T) {
	fingerprints := make(map[string]orderState)
	var mu sync.Mutex
	fp := signalFingerprint("TSLA", "CALL", "2026-04-18", "BUY", 365.0, 10)

	// First placement: PreSubmitted.
	p1, _ := simulateSignalHandling(fingerprints, &mu, fp, 42, "PreSubmitted", 1)
	if p1 != 1 {
		t.Fatalf("first signal should place; got %d", p1)
	}

	// Simulate order being cancelled externally.
	mu.Lock()
	fingerprints[fp] = orderState{OrderID: 42, Status: "Cancelled"}
	mu.Unlock()

	// A new signal arrives after cancellation — should re-place.
	p2, _ := simulateSignalHandling(fingerprints, &mu, fp, 43, "PreSubmitted", 1)
	if p2 != 1 {
		t.Errorf("after cancellation, new signal should re-place; got %d", p2)
	}
}

func TestSignalFingerprintFormat(t *testing.T) {
	fp := signalFingerprint("TSLA", "CALL", "2026-04-18", "BUY", 365.0, 10)
	expected := fmt.Sprintf("TSLA_CALL_2026-04-18_BUY_%.2f_%d", 365.0, 10)
	if fp != expected {
		t.Errorf("fingerprint = %q, want %q", fp, expected)
	}
}
