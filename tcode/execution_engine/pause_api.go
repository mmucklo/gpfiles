package main

// Phase 16.1 — Publisher pause/unpause gate
//
// Endpoints:
//   GET  /api/system/pause-status  — current pause state + remaining seconds
//   POST /api/system/pause         — pause immediately
//   POST /api/system/unpause       — body {"duration_min": 10} — unpause for N minutes
//
// Shared state: /tmp/tsla_alpha_pause_state.json
//   {"paused": true, "unpause_until": null}
//   {"paused": false, "unpause_until": "2026-04-16T14:30:00Z"}
//
// The publisher reads this file at the top of every cycle and skips all
// external API calls when paused. The Go API writes it on pause/unpause.

import (
	"encoding/json"
	"math"
	"net/http"
	"os"
	"time"
)

const pauseStateFile = "/tmp/tsla_alpha_pause_state.json"

// pauseState is the on-disk JSON shape.
type pauseState struct {
	Paused      bool    `json:"paused"`
	UnpauseUntil *string `json:"unpause_until"` // RFC3339 UTC or null
}

// pauseStatusResponse is the API response shape for GET /api/system/pause-status.
type pauseStatusResponse struct {
	Paused       bool    `json:"paused"`
	UnpauseUntil *string `json:"unpause_until"`
	RemainingSec int     `json:"remaining_sec"`
}

func readPauseState() pauseState {
	data, err := os.ReadFile(pauseStateFile)
	if err != nil {
		// No file → default paused
		return pauseState{Paused: true}
	}
	var s pauseState
	if err := json.Unmarshal(data, &s); err != nil {
		return pauseState{Paused: true}
	}
	// Check if the unpause window has expired
	if !s.Paused && s.UnpauseUntil != nil {
		until, err := time.Parse(time.RFC3339, *s.UnpauseUntil)
		if err == nil && time.Now().UTC().After(until) {
			// Expired — auto re-pause
			s.Paused = true
			s.UnpauseUntil = nil
			_ = writePauseState(s)
		}
	}
	return s
}

func writePauseState(s pauseState) error {
	data, err := json.Marshal(s)
	if err != nil {
		return err
	}
	return os.WriteFile(pauseStateFile, data, 0644)
}

// ServePauseStatus handles GET /api/system/pause-status
func (ch *ConfigHandler) ServePauseStatus(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodGet {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	s := readPauseState()
	resp := pauseStatusResponse{
		Paused:       s.Paused,
		UnpauseUntil: s.UnpauseUntil,
		RemainingSec: 0,
	}
	if !s.Paused && s.UnpauseUntil != nil {
		until, err := time.Parse(time.RFC3339, *s.UnpauseUntil)
		if err == nil {
			rem := until.Sub(time.Now().UTC()).Seconds()
			if rem > 0 {
				resp.RemainingSec = int(math.Round(rem))
			}
		}
	}
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(resp)
}

// ServePause handles POST /api/system/pause — pauses immediately
func (ch *ConfigHandler) ServePause(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	s := pauseState{Paused: true, UnpauseUntil: nil}
	if err := writePauseState(s); err != nil {
		http.Error(w, "failed to write pause state", http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	resp := pauseStatusResponse{Paused: true, UnpauseUntil: nil, RemainingSec: 0}
	_ = json.NewEncoder(w).Encode(resp)
}

// ServeUnpause handles POST /api/system/unpause — body {"duration_min": 10}
func (ch *ConfigHandler) ServeUnpause(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "method not allowed", http.StatusMethodNotAllowed)
		return
	}
	var body struct {
		DurationMin int `json:"duration_min"`
	}
	body.DurationMin = 10 // default
	if r.Body != nil {
		_ = json.NewDecoder(r.Body).Decode(&body)
	}
	if body.DurationMin < 1 {
		body.DurationMin = 1
	}
	if body.DurationMin > 480 {
		body.DurationMin = 480 // cap at 8 hours
	}
	until := time.Now().UTC().Add(time.Duration(body.DurationMin) * time.Minute)
	untilStr := until.Format(time.RFC3339)
	s := pauseState{Paused: false, UnpauseUntil: &untilStr}
	if err := writePauseState(s); err != nil {
		http.Error(w, "failed to write pause state", http.StatusInternalServerError)
		return
	}
	rem := int(math.Round(until.Sub(time.Now().UTC()).Seconds()))
	w.Header().Set("Content-Type", "application/json")
	resp := pauseStatusResponse{Paused: false, UnpauseUntil: &untilStr, RemainingSec: rem}
	_ = json.NewEncoder(w).Encode(resp)
}

// watchdogStatusFile is where pause_leak_detector.py writes its status JSON.
const watchdogStatusFile = "/tmp/pause_watchdog_status.json"

// ServeWatchdogStatus handles GET /api/pause/watchdog-status.
//
// Reads /tmp/pause_watchdog_status.json written by the pause_leak_detector.py
// daemon and returns it verbatim. Returns a safe default {"ok":true,"leak_count":0}
// when the daemon hasn't written the file yet (e.g. right after startup).
func (ch *ConfigHandler) ServeWatchdogStatus(w http.ResponseWriter, r *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Access-Control-Allow-Origin", "*")
	if r.Method != http.MethodGet {
		http.Error(w, `{"error":"method not allowed"}`, http.StatusMethodNotAllowed)
		return
	}
	data, err := os.ReadFile(watchdogStatusFile)
	if err != nil {
		// Daemon not yet running or file not written — safe default
		w.Write([]byte(`{"ok":true,"paused":false,"leak_count":0,"leaks":[],"last_checked":0}`))
		return
	}
	w.Write(data)
}
