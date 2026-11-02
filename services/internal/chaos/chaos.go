// Package chaos — хаос-режим для демо: «ломаем» СМЭВ и БКИ вживую.
//
// GET/PUT /chaos без подписи, но только с localhost и сетей из CHAOS_ALLOW_NETS.
package chaos

import (
	"encoding/json"
	"math/rand/v2"
	"net"
	"net/http"
	"os"
	"strings"
	"sync"
	"time"

	"sad/services/internal/svc"
)

const (
	SlowDefault = 4000 * time.Millisecond // slow без delay_ms
	TimeoutHold = 30 * time.Second        // timeout: висим столько, клиент уже сдался по своему таймауту
	maxDelayMS  = 60000
	LocalNets   = "127.0.0.1/32,::1/128"
)

var modes = map[string]bool{"normal": true, "slow": true, "timeout": true, "error": true, "bad_signature": true, "flaky": true}

// State — текущий режим (живёт в памяти процесса).
type State struct {
	Mode    string `json:"mode"`
	DelayMS int    `json:"delay_ms"`
}

type Controller struct {
	mu    sync.RWMutex
	state State
	nets  []*net.IPNet
}

// New читает CHAOS_ALLOW_NETS (в Docker сюда добавляют сеть compose).
func New() (*Controller, error) {
	text := os.Getenv("CHAOS_ALLOW_NETS")
	if strings.TrimSpace(text) == "" {
		text = LocalNets
	}
	return NewWithNets(text)
}

func NewWithNets(text string) (*Controller, error) {
	c := &Controller{state: State{Mode: "normal"}}
	// localhost разрешён всегда, остальное — из списка
	for _, part := range strings.Split(LocalNets+","+text, ",") {
		part = strings.TrimSpace(part)
		if part == "" {
			continue
		}
		_, n, err := net.ParseCIDR(part)
		if err != nil {
			return nil, err
		}
		c.nets = append(c.nets, n)
	}
	return c, nil
}

func (c *Controller) current() State {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return c.state
}

func (c *Controller) allowed(r *http.Request) bool {
	host, _, err := net.SplitHostPort(r.RemoteAddr)
	if err != nil {
		return false
	}
	ip := net.ParseIP(host)
	if ip == nil {
		return false
	}
	for _, n := range c.nets {
		if n.Contains(ip) {
			return true
		}
	}
	return false
}

// Register вешает GET/PUT /chaos на mux.
func (c *Controller) Register(mux *http.ServeMux, service string) {
	mux.HandleFunc("/chaos", func(w http.ResponseWriter, r *http.Request) {
		if !c.allowed(r) {
			svc.WriteJSON(w, http.StatusForbidden, "", map[string]string{"error": "forbidden"})
			return
		}
		switch r.Method {
		case http.MethodGet:
			svc.WriteJSON(w, http.StatusOK, "", c.current())
		case http.MethodPut:
			body, ok := svc.ReadBody(w, r)
			if !ok {
				return
			}
			var s State
			if err := json.Unmarshal(body, &s); err != nil || !modes[s.Mode] || s.DelayMS < 0 || s.DelayMS > maxDelayMS {
				svc.WriteJSON(w, http.StatusUnprocessableEntity, "", map[string]string{"error": "validation_error"})
				return
			}
			c.mu.Lock()
			c.state = s
			c.mu.Unlock()
			slogChange(service, s)
			svc.WriteJSON(w, http.StatusOK, "", s)
		default:
			svc.WriteJSON(w, http.StatusMethodNotAllowed, "", map[string]string{"error": "method_not_allowed"})
		}
	})
}

// Before — хук для svc.SignedPost: применяет текущий режим к запросу.
// Вернёт done=true, если уже ответил (error/timeout/flaky); signWith — чужой секрет для режима bad_signature.
func (c *Controller) Before(secret string) func(http.ResponseWriter, *http.Request) (bool, string) {
	return func(w http.ResponseWriter, r *http.Request) (bool, string) {
		s := c.current()
		switch s.Mode {
		case "slow":
			d := SlowDefault
			if s.DelayMS > 0 {
				d = time.Duration(s.DelayMS) * time.Millisecond
			}
			sleep(r, d)
		case "error":
			svc.WriteJSON(w, http.StatusServiceUnavailable, secret, map[string]string{"error": "service_unavailable"})
			return true, ""
		case "timeout":
			sleep(r, TimeoutHold)
			svc.WriteJSON(w, http.StatusGatewayTimeout, secret, map[string]string{"error": "gateway_timeout"})
			return true, ""
		case "flaky":
			if rand.Float64() < 0.5 {
				svc.WriteJSON(w, http.StatusServiceUnavailable, secret, map[string]string{"error": "service_unavailable"})
				return true, ""
			}
		case "bad_signature":
			return false, secret + "-wrong" // ответ подписан чужим секретом
		}
		return false, ""
	}
}

// sleep ждёт d или until клиент отключится (тогда висеть дальше незачем).
func sleep(r *http.Request, d time.Duration) {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-t.C:
	case <-r.Context().Done():
	}
}
