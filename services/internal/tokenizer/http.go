package tokenizer

import (
	"encoding/base64"
	"encoding/json"
	"net/http"

	"sad/services/internal/svc"
)

// Register вешает на mux GET /health, GET /oprf/v1/public-key и POST /oprf/v1/evaluate.
// Запрос evaluate должен быть подписан секретом; ответы обоих методов подписаны им же.
func Register(mux *http.ServeMux, tk *Tokenizer, secret string) {
	mux.HandleFunc("GET /health", svc.Health("tokenizer"))

	mux.HandleFunc("GET "+PublicKeyPath, func(w http.ResponseWriter, r *http.Request) {
		svc.WriteJSON(w, http.StatusOK, secret, map[string]string{
			"suite": SuiteName, "public_key": base64.StdEncoding.EncodeToString(tk.PublicKey())})
	})

	mux.HandleFunc("POST "+EvaluatePath, svc.SignedPost(secret, nil, func(body []byte) (any, []any, error) {
		var req struct {
			Blinded string `json:"blinded"`
		}
		bad := &svc.HTTPError{Status: http.StatusBadRequest, Code: "bad_blinded"}
		if json.Unmarshal(body, &req) != nil {
			return nil, nil, bad
		}
		blinded, err := base64.StdEncoding.DecodeString(req.Blinded)
		if err != nil {
			return nil, nil, bad
		}
		evaluated, proof, err := tk.Evaluate(blinded)
		if err != nil {
			return nil, nil, bad
		}
		// в лог ничего из запроса не пишем: даже ослеплённое число не нужно хранить
		return map[string]string{
			"evaluated": base64.StdEncoding.EncodeToString(evaluated),
			"proof":     base64.StdEncoding.EncodeToString(proof),
		}, []any{"endpoint", "evaluate"}, nil
	}))
}
