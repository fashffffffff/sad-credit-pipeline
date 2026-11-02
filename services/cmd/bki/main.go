// Эмулятор БКИ. Порт BKI_PORT (8102), секрет подписи BKI_HMAC_SECRET.
//
//	POST /bki/score  {passport_hash} → {score, delinquency, requests_90d}  — формула организаторов
//	POST /bki/report {passport_hash} → {found, loans: [...]}               — история для ML-подсказки
//
// Обработчики — в пакете sad/services/bki.
package main

import (
	"net/http"

	"sad/services/bki"
	"sad/services/internal/chaos"
	"sad/services/internal/svc"
)

func main() {
	svc.InitLogger("bki")
	secret, err := svc.SecretFromEnv("BKI_HMAC_SECRET", "dev-bki-secret")
	if err != nil {
		svc.Fatal(err)
	}
	pool, err := bki.LoadEmbedded()
	if err != nil {
		svc.Fatal(err)
	}
	ctl, err := chaos.New()
	if err != nil {
		svc.Fatal(err)
	}
	mux := http.NewServeMux()
	bki.Register(mux, secret, pool, ctl)
	if err := svc.Run("bki", "BKI_PORT", "8102", mux); err != nil {
		svc.Fatal(err)
	}
}
