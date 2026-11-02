// Токенизатор паспорта (OPRF, RFC 9497, P256-SHA256). Порт TOKENIZER_PORT (8103), слушает только BIND_HOST (по умолчанию 127.0.0.1).
//
//	POST /oprf/v1/evaluate  {"blinded": base64} → {"evaluated": base64, "proof": base64}  (запрос подписан backend)
//	GET  /oprf/v1/public-key → {"suite": "P256-SHA256", "public_key": base64}               (запрос без подписи, ответ подписан)
//
// Ключ: OPRF_PRIVATE_KEY (base64) или файл OPRF_KEY_FILE (по умолчанию secrets/oprf.key, создаётся при первом старте).
// Подпись запросов и ответов: TOKENIZER_HMAC_SECRET.
package main

import (
	"net/http"
	"os"

	"sad/services/internal/svc"
	"sad/services/internal/tokenizer"
)

func main() {
	svc.InitLogger("tokenizer")
	secret, err := svc.SecretFromEnv("TOKENIZER_HMAC_SECRET", "dev-tokenizer-secret")
	if err != nil {
		svc.Fatal(err)
	}
	keyFile := os.Getenv("OPRF_KEY_FILE")
	if keyFile == "" {
		keyFile = "secrets/oprf.key"
	}
	rawKey, err := tokenizer.LoadKey(os.Getenv("OPRF_PRIVATE_KEY"), keyFile)
	if err != nil {
		svc.Fatal(err)
	}
	tk, err := tokenizer.New(rawKey)
	if err != nil {
		svc.Fatal(err)
	}

	mux := http.NewServeMux()
	tokenizer.Register(mux, tk, secret)
	if err := svc.Run("tokenizer", "TOKENIZER_PORT", "8103", mux); err != nil {
		svc.Fatal(err)
	}
}
