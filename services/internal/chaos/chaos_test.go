package chaos

import (
	"context"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
)

func put(mux *http.ServeMux, remote, body string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(http.MethodPut, "/chaos", strings.NewReader(body))
	req.RemoteAddr = remote
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	return rec
}

func TestAccessAndValidation(t *testing.T) {
	ctl, _ := NewWithNets("172.30.0.0/24")
	mux := http.NewServeMux()
	ctl.Register(mux, "test")

	if rec := put(mux, "127.0.0.1:5000", `{"mode":"slow","delay_ms":10}`); rec.Code != 200 {
		t.Errorf("localhost: %d", rec.Code)
	}
	if rec := put(mux, "[::1]:5000", `{"mode":"normal"}`); rec.Code != 200 {
		t.Errorf("::1: %d", rec.Code)
	}
	if rec := put(mux, "172.30.0.5:5000", `{"mode":"error"}`); rec.Code != 200 {
		t.Errorf("сеть из CHAOS_ALLOW_NETS: %d", rec.Code)
	}
	if rec := put(mux, "8.8.8.8:5000", `{"mode":"normal"}`); rec.Code != 403 {
		t.Errorf("чужой адрес должен получить 403, а не %d", rec.Code)
	}
	if rec := put(mux, "127.0.0.1:5000", `{"mode":"explode"}`); rec.Code != 422 {
		t.Errorf("неизвестный режим: %d", rec.Code)
	}
	if rec := put(mux, "127.0.0.1:5000", `{"mode":"slow","delay_ms":-1}`); rec.Code != 422 {
		t.Errorf("отрицательная задержка: %d", rec.Code)
	}

	req := httptest.NewRequest(http.MethodGet, "/chaos", nil)
	req.RemoteAddr = "127.0.0.1:1"
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, req)
	if !strings.Contains(rec.Body.String(), `"mode":"error"`) {
		t.Errorf("GET должен вернуть последний режим (error): %s", rec.Body.String())
	}
}

func TestBefore(t *testing.T) {
	ctl, _ := NewWithNets("")
	before := ctl.Before("secret")
	run := func() (*httptest.ResponseRecorder, bool, string) {
		rec := httptest.NewRecorder()
		done, alt := before(rec, httptest.NewRequest(http.MethodPost, "/x", nil))
		return rec, done, alt
	}

	if _, done, alt := run(); done || alt != "" {
		t.Error("normal не должен вмешиваться")
	}
	ctl.state = State{Mode: "error"}
	if rec, done, _ := run(); !done || rec.Code != 503 {
		t.Errorf("error: done=%v code=%d", done, rec.Code)
	}
	ctl.state = State{Mode: "bad_signature"}
	if _, done, alt := run(); done || alt != "secret-wrong" {
		t.Errorf("bad_signature: done=%v alt=%q", done, alt)
	}
	ctl.state = State{Mode: "slow", DelayMS: 50}
	started := time.Now()
	if _, done, _ := run(); done || time.Since(started) < 50*time.Millisecond {
		t.Error("slow должен ждать delay_ms и потом отвечать штатно")
	}
	ctl.state = State{Mode: "flaky"}
	fails := 0
	for i := 0; i < 200; i++ {
		if _, done, _ := run(); done {
			fails++
		}
	}
	if fails < 60 || fails > 140 {
		t.Errorf("flaky: %d отказов из 200, ждали около половины", fails)
	}
}

func TestTimeoutStopsWhenClientLeaves(t *testing.T) {
	ctl, _ := NewWithNets("")
	ctl.state = State{Mode: "timeout"}
	req := httptest.NewRequest(http.MethodPost, "/x", nil)
	ctx, cancel := contextWithCancel(req)
	req = req.WithContext(ctx)
	go func() { time.Sleep(50 * time.Millisecond); cancel() }()
	started := time.Now()
	ctl.Before("s")(httptest.NewRecorder(), req)
	if time.Since(started) > 5*time.Second {
		t.Error("после отключения клиента ждать 30 с незачем")
	}
}

func contextWithCancel(r *http.Request) (ctx context.Context, cancel context.CancelFunc) {
	return context.WithCancel(r.Context())
}
