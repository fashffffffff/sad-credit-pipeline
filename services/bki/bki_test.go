package bki

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"sad/services/internal/chaos"
	"sad/services/internal/emulation"
	"sad/services/internal/signing"
	"sad/services/internal/svc"
)

const secret = "test-bki-secret"

func newMux(t *testing.T) *http.ServeMux {
	t.Helper()
	pool, err := LoadEmbedded()
	if err != nil {
		t.Fatal(err)
	}
	ctl, _ := chaos.NewWithNets("")
	mux := http.NewServeMux()
	Register(mux, secret, pool, ctl)
	return mux
}

func post(mux *http.ServeMux, path, body string) *httptest.ResponseRecorder {
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, svc.SignedRequest("POST", path, secret, body))
	return rec
}

func TestPoolShape(t *testing.T) {
	pool, err := LoadEmbedded()
	if err != nil {
		t.Fatal(err)
	}
	for _, b := range []string{emulation.Good, emulation.Medium, emulation.Bad} {
		if pool.Size(b) < 100 {
			t.Errorf("в корзине %s всего %d историй", b, pool.Size(b))
		}
	}
	// у кредита нет поля id, есть rn
	var loans []map[string]int
	if err := json.Unmarshal(pool.History("a1b2c3d4e5f6a7b8"), &loans); err != nil || len(loans) == 0 {
		t.Fatalf("история не разобралась: %v", err)
	}
	if _, has := loans[0]["id"]; has {
		t.Error("поле id должно быть удалено")
	}
	if _, has := loans[0]["rn"]; !has {
		t.Error("поле rn (порядок кредитов) должно остаться")
	}
}

func TestScore(t *testing.T) {
	mux := newMux(t)
	rec := post(mux, "/bki/score", `{"passport_hash":"a1b2c3d4e5f6a7b8"}`)
	if rec.Code != 200 {
		t.Fatalf("%d %s", rec.Code, rec.Body)
	}
	var got emulation.BkiScore
	_ = json.Unmarshal(rec.Body.Bytes(), &got)
	if got != emulation.Score("a1b2c3d4e5f6a7b8") {
		t.Errorf("ответ %+v не совпал с формулой", got)
	}
	// ключи ответа — как в контракте
	var raw map[string]any
	_ = json.Unmarshal(rec.Body.Bytes(), &raw)
	for _, k := range []string{"score", "delinquency", "requests_90d"} {
		if _, ok := raw[k]; !ok {
			t.Errorf("в ответе нет поля %s", k)
		}
	}
	if !signing.Verify(secret, rec.Body.Bytes(), rec.Header().Get(signing.SignatureHeader),
		rec.Header().Get(signing.TimestampHeader), time.Now()) {
		t.Error("ответ не подписан")
	}
}

func TestReportMatchesBucketAndIsDeterministic(t *testing.T) {
	mux := newMux(t)
	pool, _ := LoadEmbedded()
	for _, h := range []string{"a1b2c3d4e5f6a7b8", "0000000000000000", "ffffffffffffffff", "f727a849055f2224"} {
		first := post(mux, "/bki/report", `{"passport_hash":"`+h+`"}`)
		second := post(mux, "/bki/report", `{"passport_hash":"`+h+`"}`)
		if first.Code != 200 || first.Body.String() != second.Body.String() {
			t.Errorf("%s: ответ должен быть детерминирован", h)
		}
		var resp struct {
			Found bool              `json:"found"`
			Loans []json.RawMessage `json:"loans"`
		}
		if err := json.Unmarshal(first.Body.Bytes(), &resp); err != nil || !resp.Found || len(resp.Loans) == 0 {
			t.Fatalf("%s: плохой ответ %v", h, err)
		}
		want := pool.History(h)
		var wantLoans []json.RawMessage
		_ = json.Unmarshal(want, &wantLoans)
		if len(wantLoans) != len(resp.Loans) {
			t.Errorf("%s: в ответе %d кредитов, в пуле %d", h, len(resp.Loans), len(wantLoans))
		}
	}
}

func TestRejects(t *testing.T) {
	mux := newMux(t)
	if rec := post(mux, "/bki/score", `{}`); rec.Code != 422 {
		t.Errorf("пустой хэш: %d", rec.Code)
	}
	if rec := post(mux, "/bki/report", `мусор`); rec.Code != 422 {
		t.Errorf("не JSON: %d", rec.Code)
	}
	rec := httptest.NewRecorder()
	req := svc.SignedRequest("POST", "/bki/score", "чужой", `{"passport_hash":"x"}`)
	mux.ServeHTTP(rec, req)
	if rec.Code != 401 {
		t.Errorf("чужая подпись: %d", rec.Code)
	}
}
