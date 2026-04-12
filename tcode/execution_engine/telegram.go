package main

import (
	"fmt"
	"net/http"
	"net/url"
)

// TelegramBot handles high-priority alerts for the Phase 4 Kill-Switch.
type TelegramBot struct {
	Token  string
	ChatID string
}

func NewTelegramBot(token, chatID string) *TelegramBot {
	return &TelegramBot{Token: token, ChatID: chatID}
}

// SendAlert broadcasts a message to the human operator.
// Why: Critical failure modes (Kill-Switch) require immediate human intervention
// and cannot be resolved by AI agents alone.
func (b *TelegramBot) SendAlert(message string) {
	if b.Token == "" || b.ChatID == "" {
		fmt.Printf("TELEGRAM (Stub): %s\n", message)
		return
	}

	apiURL := fmt.Sprintf("https://api.telegram.org/bot%s/sendMessage", b.Token)
	resp, err := http.PostForm(apiURL, url.Values{
		"chat_id": {b.ChatID},
		"text":    {message},
	})

	if err != nil {
		fmt.Printf("Telegram Send Error: %v\n", err)
		return
	}
	defer resp.Body.Close()
	fmt.Println("Telegram Alert Dispatched Successfully.")
}
