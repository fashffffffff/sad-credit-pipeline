// Package smev — обработчики эмулятора СМЭВ: POST /smev/check {client_id, passport_hash} → {"status": "MATCH"|"MISMATCH"}.
package smev

import (
	"encoding/json"
	"net/http"

	"sad/services/internal/chaos"
	"sad/services/internal/emulation"
	"sad/services/internal/svc"
)

type checkRequest struct {
	ClientID     string `json:"client_id"`
	PassportHash string `json:"passport_hash"`
}

// Register вешает на mux /health, /chaos и /smev/check.
func Register(mux *http.ServeMux, secret string, ctl *chaos.Controller) {
	mux.HandleFunc("GET /health", svc.Health("smev"))
	ctl.Register(mux, "smev")
	mux.HandleFunc("POST /smev/check", svc.SignedPost(secret, ctl.Before(secret), func(body []byte) (any, []any, error) {
		var req checkRequest
		if err := json.Unmarshal(body, &req); err != nil || req.ClientID == "" || req.PassportHash == "" {
			return nil, nil, &svc.HTTPError{Status: http.StatusUnprocessableEntity, Code: "validation_error"}
		}
		status := emulation.SmevStatus(req.ClientID)
		// в лог — только идентификатор клиента (не ПДн) и первые 6 символов хэша
		return map[string]string{"status": status},
			[]any{"client_id", req.ClientID, "passport", svc.Short(req.PassportHash), "status", status}, nil
	}))
}
