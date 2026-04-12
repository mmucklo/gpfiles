package main

import (
	"database/sql"
	"log"
	"time"

	_ "github.com/lib/pq"
)

// ArchiveSink handles the persistent storage of every market tick, signal, and trade.
type ArchiveSink struct {
	DB *sql.DB
}

func NewArchiveSink(connStr string) (*ArchiveSink, error) {
	db, err := sql.Open("postgres", connStr)
	if err != nil {
		return nil, err
	}
	
	// Ensure tables exist (TimescaleDB hypertable logic in production)
	schema := `
	CREATE TABLE IF NOT EXISTS signals (
		time TIMESTAMPTZ NOT NULL,
		model_id TEXT,
		direction TEXT,
		confidence DOUBLE PRECISION
	);
	CREATE TABLE IF NOT EXISTS trades (
		time TIMESTAMPTZ NOT NULL,
		ticker TEXT,
		action TEXT,
		quantity INT,
		price DOUBLE PRECISION,
		pnl DOUBLE PRECISION
	);
	`
	_, err = db.Exec(schema)
	if err != nil {
		return nil, err
	}

	return &ArchiveSink{DB: db}, nil
}

func (s *ArchiveSink) RecordSignal(modelID, direction string, confidence float64) {
	_, err := s.DB.Exec("INSERT INTO signals (time, model_id, direction, confidence) VALUES ($1, $2, $3, $4)",
		time.Now(), modelID, direction, confidence)
	if err != nil {
		log.Printf("Archive Error (Signal): %v", err)
	}
}

func (s *ArchiveSink) RecordTrade(ticker, action string, quantity int, price, pnl float64) {
	_, err := s.DB.Exec("INSERT INTO trades (time, ticker, action, quantity, price, pnl) VALUES ($1, $2, $3, $4, $5, $6)",
		time.Now(), ticker, action, quantity, price, pnl)
	if err != nil {
		log.Printf("Archive Error (Trade): %v", err)
	}
}
