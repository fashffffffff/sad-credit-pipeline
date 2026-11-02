package signing

import (
	"testing"
	"time"
)

// Векторы посчитаны Python-реализацией:
//
//	uv run python -c "from common.signing import sign; print(sign('dev-bki-secret', b'{...}', 1700000000))"
var vectors = []struct {
	secret, body string
	ts           int64
	want         string
}{
	{"dev-bki-secret", `{"passport_hash":"a1b2c3d4e5f6a7b8"}`, 1700000000,
		"ae1e5095420323732e8b0eca2acb02adb54bbb50330cdb7da86d6bb52d1dc2a9"},
	{"секрет-ключ", "тело: Привет", 1,
		"0d49e68a719c7c8e3aa9b1d66438bf5d7784060a1b8331f633e373536d662f1f"},
	{"k", "", 0,
		"6b4a4b8b3c40f1e8f53a3d36682e5f99f7ad2ac1df1c93dfe336f329167641e7"},
}

func TestSignMatchesPython(t *testing.T) {
	for _, v := range vectors {
		got, err := Sign(v.secret, []byte(v.body), v.ts)
		if err != nil || got != v.want {
			t.Errorf("Sign(%q, %q, %d) = %q, %v; ждали %q", v.secret, v.body, v.ts, got, err, v.want)
		}
	}
}

func TestSignEmptySecret(t *testing.T) {
	if _, err := Sign("", []byte("x"), 1); err == nil {
		t.Error("пустой секрет должен давать ошибку")
	}
}

func TestVerify(t *testing.T) {
	v := vectors[0]
	now := time.Unix(v.ts, 0)
	body := []byte(v.body)
	ts := "1700000000"

	if !Verify(v.secret, body, v.want, ts, now) {
		t.Error("верная подпись не принята")
	}
	if !Verify(v.secret, body, v.want, ts, now.Add(299*time.Second)) {
		t.Error("в окне 300 с подпись должна приниматься")
	}
	if Verify(v.secret, body, v.want, ts, now.Add(301*time.Second)) {
		t.Error("старое сообщение (replay) принято")
	}
	if Verify(v.secret, body, v.want, ts, now.Add(-301*time.Second)) {
		t.Error("сообщение из будущего за окном принято")
	}
	if Verify(v.secret, []byte(`{"passport_hash":"x"}`), v.want, ts, now) {
		t.Error("подмена тела не замечена")
	}
	if Verify("другой-секрет", body, v.want, ts, now) {
		t.Error("чужой секрет принят")
	}
	if Verify(v.secret, body, "", ts, now) || Verify(v.secret, body, v.want, "", now) ||
		Verify(v.secret, body, v.want, "не-число", now) || Verify("", body, v.want, ts, now) {
		t.Error("пустые/битые заголовки приняты")
	}
	if Verify(v.secret, body, "тест-юникод", ts, now) {
		t.Error("не-ASCII подпись принята")
	}
}
