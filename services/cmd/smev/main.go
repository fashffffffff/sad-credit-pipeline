// Эмулятор СМЭВ (госданные). Порт SMEV_PORT (8101), секрет подписи SMEV_HMAC_SECRET. Обработчики — в internal/smev.
package main

import (
	"net/http"

	"sad/services/internal/chaos"
	"sad/services/internal/smev"
	"sad/services/internal/svc"
)

func main() {
	svc.InitLogger("smev")
	secret, err := svc.SecretFromEnv("SMEV_HMAC_SECRET", "dev-smev-secret")
	if err != nil {
		svc.Fatal(err)
	}
	ctl, err := chaos.New()
	if err != nil {
		svc.Fatal(err)
	}
	mux := http.NewServeMux()
	smev.Register(mux, secret, ctl)
	if err := svc.Run("smev", "SMEV_PORT", "8101", mux); err != nil {
		svc.Fatal(err)
	}
}
