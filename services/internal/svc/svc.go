// Package svc — общий каркас Go-сервисов: логи без ПДн, лимит тела, подписанные ответы, /health, запуск.
package svc

import (
	"context"
	"encoding/json"
	"errors"
	"io"
	"log/slog"
	"net"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"sad/services/internal/signing"
)

// MaxBodyBytes — лимит тела запроса (как BodyLimitMiddleware в Python).
const MaxBodyBytes = 64 * 1024

// InitLogger включает JSON-логи в stdout. Без ПДн: сюда пишем только служебные поля.
func InitLogger(service string) {
	h := slog.NewJSONHandler(os.Stdout, nil)
	slog.SetDefault(slog.New(h).With("service", service))
}

// Short — первые 6 символов хэша для логов (полный хэш в лог не пишем).
func Short(s string) string {
	if len(s) > 6 {
		return s[:6]
	}
	return s
}

// HTTPError — ошибка с кодом ответа, её можно вернуть из обработчика.
type HTTPError struct {
	Status int
	Code   string
}

func (e *HTTPError) Error() string { return e.Code }

// WriteJSON отправляет JSON. Если secret не пуст — подписывает именно те байты, которые отправляет.
func WriteJSON(w http.ResponseWriter, status int, secret string, v any) {
	body, err := json.Marshal(v)
	if err != nil {
		http.Error(w, `{"error":"internal"}`, http.StatusInternalServerError)
		return
	}
	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	if secret != "" {
		if err := signing.SetHeaders(w.Header(), secret, body); err != nil {
			http.Error(w, `{"error":"internal"}`, http.StatusInternalServerError)
			return
		}
	}
	w.WriteHeader(status)
	_, _ = w.Write(body)
}

// ReadBody читает тело не больше MaxBodyBytes. Второй результат false — тело слишком большое (ответ 413 уже отправлен).
func ReadBody(w http.ResponseWriter, r *http.Request) ([]byte, bool) {
	body, err := io.ReadAll(http.MaxBytesReader(w, r.Body, MaxBodyBytes))
	if err != nil {
		WriteJSON(w, http.StatusRequestEntityTooLarge, "", map[string]string{"error": "body_too_large"})
		return nil, false
	}
	return body, true
}

// Health отвечает на GET /health.
func Health(service string) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		WriteJSON(w, http.StatusOK, "", map[string]string{"status": "ok", "service": service})
	}
}

// Handler обрабатывает уже проверенное по подписи тело. Возвращает ответ и дополнительные поля для лога.
type Handler func(body []byte) (resp any, logAttrs []any, err error)

// SignedPost собирает POST-обработчик: лимит тела → проверка подписи запроса → (before) → handler → подписанный ответ.
// before — необязательный хук (хаос-режим): вернёт true, если сам ответил, и тогда handler не вызывается;
// вторым значением может вернуть секрет для подписи ответа (по умолчанию — штатный).
func SignedPost(secret string, before func(w http.ResponseWriter, r *http.Request) (done bool, signWith string), h Handler) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		body, ok := ReadBody(w, r)
		if !ok {
			return
		}
		if !signing.VerifyRequest(secret, body, r) {
			WriteJSON(w, http.StatusUnauthorized, "", map[string]string{"error": "bad_signature"})
			return
		}
		signWith := secret
		if before != nil {
			done, alt := before(w, r)
			if done {
				return
			}
			if alt != "" {
				signWith = alt
			}
		}
		resp, attrs, err := h(body)
		if err != nil {
			var he *HTTPError
			if errors.As(err, &he) {
				WriteJSON(w, he.Status, secret, map[string]string{"error": he.Code})
			} else {
				WriteJSON(w, http.StatusInternalServerError, secret, map[string]string{"error": "internal"})
			}
			return
		}
		if len(attrs) > 0 {
			slog.Info("обработан запрос", attrs...)
		}
		WriteJSON(w, http.StatusOK, signWith, resp)
	}
}

// logRequests пишет одну JSON-строку на запрос: метод, путь, код, время. Тел и заголовков в логе нет.
func logRequests(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		started := time.Now()
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r)
		slog.Info("запрос", "method", r.Method, "path", r.URL.Path, "status", rec.status,
			"ms", time.Since(started).Milliseconds())
	})
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (s *statusRecorder) WriteHeader(code int) {
	s.status = code
	s.ResponseWriter.WriteHeader(code)
}

// Run запускает сервер на BIND_HOST:порт (по умолчанию 127.0.0.1) и останавливает его по Ctrl+C / SIGTERM.
func Run(service, portEnv, defaultPort string, mux http.Handler) error {
	host := os.Getenv("BIND_HOST")
	if host == "" {
		host = "127.0.0.1"
	}
	port := os.Getenv(portEnv)
	if port == "" {
		port = defaultPort
	}
	srv := &http.Server{
		Addr:              net.JoinHostPort(host, port),
		Handler:           logRequests(mux),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		IdleTimeout:       60 * time.Second,
		// WriteTimeout не ставим: хаос-режим timeout специально держит ответ 30 с
	}
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	errCh := make(chan error, 1)
	go func() { errCh <- srv.ListenAndServe() }()
	slog.Info("сервис запущен", "addr", srv.Addr)

	select {
	case err := <-errCh:
		return err
	case <-ctx.Done():
		shutdownCtx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		return srv.Shutdown(shutdownCtx)
	}
}

// Fatal пишет ошибку старта в лог и завершает процесс с кодом 1.
func Fatal(err error) {
	slog.Error("сервис не запустился", "error", err.Error())
	os.Exit(1)
}
