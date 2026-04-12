package main

import (
	"fmt"
	"net/http"
	"sync"

	"github.com/gorilla/websocket"
)

var upgrader = websocket.Upgrader{
	CheckOrigin: func(r *http.Request) bool { return true },
}

type ReloadHub struct {
	clients map[*websocket.Conn]bool
	mu      sync.Mutex
}

func NewReloadHub() *ReloadHub {
	return &ReloadHub{
		clients: make(map[*websocket.Conn]bool),
	}
}

func (h *ReloadHub) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	conn, err := upgrader.Upgrade(w, r, nil)
	if err != nil {
		return
	}
	h.mu.Lock()
	h.clients[conn] = true
	h.mu.Unlock()

	defer func() {
		h.mu.Lock()
		delete(h.clients, conn)
		h.mu.Unlock()
		conn.Close()
	}()

	// Keep connection open
	for {
		if _, _, err := conn.ReadMessage(); err != nil {
			break
		}
	}
}

func (h *ReloadHub) TriggerReload() {
	h.mu.Lock()
	defer h.mu.Unlock()
	fmt.Printf("Triggering UI reload for %d clients...\n", len(h.clients))
	for client := range h.clients {
		client.WriteMessage(websocket.TextMessage, []byte("reload"))
	}
}

var GlobalReloader = NewReloadHub()

func TriggerReloadHandler(w http.ResponseWriter, r *http.Request) {
	GlobalReloader.TriggerReload()
	w.Write([]byte("ok"))
}
