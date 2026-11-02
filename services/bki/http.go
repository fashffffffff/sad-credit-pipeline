package bki

import (
	"encoding/json"
	"net/http"

	"sad/services/internal/chaos"
	"sad/services/internal/emulation"
	"sad/services/internal/svc"
)

type request struct {
	PassportHash string `json:"passport_hash"`
}

// parse достаёт passport_hash из тела; пустой хэш — ошибка 422.
func parse(body []byte) (string, error) {
	var req request
	if err := json.Unmarshal(body, &req); err != nil || req.PassportHash == "" {
		return "", &svc.HTTPError{Status: http.StatusUnprocessableEntity, Code: "validation_error"}
	}
	return req.PassportHash, nil
}

// Register вешает на mux /health, /chaos, POST /bki/score и POST /bki/report.
func Register(mux *http.ServeMux, secret string, pool *Pool, ctl *chaos.Controller) {
	mux.HandleFunc("GET /health", svc.Health("bki"))
	ctl.Register(mux, "bki")

	// скор по формуле организаторов
	mux.HandleFunc("POST /bki/score", svc.SignedPost(secret, ctl.Before(secret), func(body []byte) (any, []any, error) {
		hash, err := parse(body)
		if err != nil {
			return nil, nil, err
		}
		res := emulation.Score(hash)
		return res, []any{"endpoint", "score", "passport", svc.Short(hash), "score", res.Score}, nil
	}))

	// история для ML-подсказки: корзина и номер истории считаются из того же md5, что и скор
	mux.HandleFunc("POST /bki/report", svc.SignedPost(secret, ctl.Before(secret), func(body []byte) (any, []any, error) {
		hash, err := parse(body)
		if err != nil {
			return nil, nil, err
		}
		bucket, _ := emulation.Bucket(hash)
		resp := struct {
			Found bool            `json:"found"`
			Loans json.RawMessage `json:"loans"`
		}{Found: true, Loans: pool.History(hash)}
		return resp, []any{"endpoint", "report", "passport", svc.Short(hash), "bucket", bucket}, nil
	}))
}
