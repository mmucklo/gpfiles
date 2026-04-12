# Multi-stage build for Go Execution Engine
FROM golang:1.24-bookworm AS builder

WORKDIR /app
COPY execution_engine/go.mod execution_engine/go.sum ./
RUN go mod download

COPY execution_engine/ ./
RUN go build -o alpha_executor .

FROM debian:bookworm-slim
WORKDIR /app
COPY --from=builder /app/alpha_executor .
# Install CA certificates for IBKR/Telegram HTTPS connections
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*

ENTRYPOINT ["./alpha_executor"]
