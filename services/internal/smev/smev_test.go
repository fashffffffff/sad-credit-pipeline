package smev

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"sad/services/internal/chaos"
	"sad/services/internal/emulation"
	"sad/services/internal/svc"
)

const secret = "test-smev-secret"

func TestCheck(t *testing.T) {
	ctl, _ := chaos.NewWithNets("")
	mux := http.NewServeMux()
	Register(mux, secret, ctl)

	// MISMATCH ровно там, где md5(client_id) % 10 == 0
	counts := map[string]int{}
	for i := 1; i <= 200; i++ {
		id := "cli_" + string(rune('0'+i/100%10)) + string(rune('0'+i/10%10)) + string(rune('0'+i%10))
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, svc.SignedRequest("POST", "/smev/check", secret,
			`{"client_id":"`+id+`","passport_hash":"a1b2c3d4e5f6a7b8"}`))
		var resp map[string]string
		_ = json.Unmarshal(rec.Body.Bytes(), &resp)
		if rec.Code != 200 || resp["status"] != emulation.SmevStatus(id) {
			t.Fatalf("%s: %d %s", id, rec.Code, rec.Body)
		}
		counts[resp["status"]]++
	}
	if counts["MISMATCH"] == 0 || counts["MATCH"] < counts["MISMATCH"] {
		t.Errorf("распределение странное: %v", counts)
	}

	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, svc.SignedRequest("POST", "/smev/check", secret, `{"client_id":"cli_0001"}`))
	if rec.Code != 422 {
		t.Errorf("без passport_hash ждали 422, получили %d", rec.Code)
	}
	rec = httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("POST", "/smev/check", nil))
	if rec.Code != 401 {
		t.Errorf("без подписи ждали 401, получили %d", rec.Code)
	}
}
