package tokenizer

import (
	"bytes"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
	"time"

	"github.com/cloudflare/circl/oprf"
	"github.com/cloudflare/circl/zk/dleq"

	"sad/services/internal/signing"
	"sad/services/internal/svc"
)

const secret = "test-tokenizer-secret"

func newServer(t *testing.T) (*http.ServeMux, *Tokenizer) {
	t.Helper()
	raw, err := LoadKey("", filepath.Join(t.TempDir(), "oprf.key"))
	if err != nil {
		t.Fatal(err)
	}
	tk, err := New(raw)
	if err != nil {
		t.Fatal(err)
	}
	mux := http.NewServeMux()
	Register(mux, tk, secret)
	return mux, tk
}

// evaluate вызывает сервис по HTTP так, как это делает backend, и возвращает ответ сервера.
func evaluate(t *testing.T, mux *http.ServeMux, blinded []byte) (evaluated, proof []byte) {
	t.Helper()
	body, _ := json.Marshal(map[string]string{"blinded": base64.StdEncoding.EncodeToString(blinded)})
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, svc.SignedRequest("POST", EvaluatePath, secret, string(body)))
	if rec.Code != 200 {
		t.Fatalf("evaluate: %d %s", rec.Code, rec.Body)
	}
	// ответ тоже подписан
	if !signing.Verify(secret, rec.Body.Bytes(), rec.Header().Get(signing.SignatureHeader),
		rec.Header().Get(signing.TimestampHeader), time.Now()) {
		t.Fatal("ответ evaluate не подписан или подпись неверна")
	}
	var resp map[string]string
	_ = json.Unmarshal(rec.Body.Bytes(), &resp)
	evaluated, _ = base64.StdEncoding.DecodeString(resp["evaluated"])
	proof, _ = base64.StdEncoding.DecodeString(resp["proof"])
	return
}

// Полный круг: клиент ослепляет → токенизатор по HTTP → клиент проверяет доказательство и снимает ослепление.
func TestOprfRoundTrip(t *testing.T) {
	mux, tk := newServer(t)
	suite := oprf.SuiteP256

	var pub oprf.PublicKey
	if err := pub.UnmarshalBinary(suite, tk.PublicKey()); err != nil {
		t.Fatal(err)
	}
	client := oprf.NewVerifiableClient(suite, &pub)

	var outputs [][]byte
	var blindeds [][]byte
	for i := 0; i < 2; i++ {
		fin, req, err := client.Blind([][]byte{[]byte("4510123456")})
		if err != nil {
			t.Fatal(err)
		}
		blinded, _ := req.Elements[0].MarshalBinaryCompress()
		evaluated, proofBytes := evaluate(t, mux, blinded)

		el := suite.Group().NewElement()
		if err := el.UnmarshalBinary(evaluated); err != nil {
			t.Fatal(err)
		}
		proof := new(dleq.Proof)
		if err := proof.UnmarshalBinary(suite.Group(), proofBytes); err != nil {
			t.Fatal(err)
		}
		out, err := client.Finalize(fin, &oprf.Evaluation{Elements: []oprf.Evaluated{el}, Proof: proof})
		if err != nil { // здесь проверяется доказательство DLEQ
			t.Fatalf("клиент не принял доказательство: %v", err)
		}
		outputs = append(outputs, out[0])
		blindeds = append(blindeds, blinded)
	}
	if !bytes.Equal(outputs[0], outputs[1]) {
		t.Error("один и тот же паспорт должен давать один и тот же выход OPRF")
	}
	if bytes.Equal(blindeds[0], blindeds[1]) {
		t.Error("blinded должны быть разными при каждой попытке")
	}
	// сам сервер (с ключом) считает тот же выход напрямую — значит протокол честный
	direct, err := tk.server.FullEvaluate([]byte("4510123456"))
	if err != nil || !bytes.Equal(direct, outputs[0]) {
		t.Error("выход через протокол не совпал с прямым вычислением на сервере")
	}
}

func TestEvaluateRejectsBadRequests(t *testing.T) {
	mux, _ := newServer(t)
	good, _ := json.Marshal(map[string]string{"blinded": base64.StdEncoding.EncodeToString(make([]byte, 33))})

	// без подписи и с чужой подписью
	for _, req := range []*http.Request{
		httptest.NewRequest("POST", EvaluatePath, bytes.NewReader(good)),
		svc.SignedRequest("POST", EvaluatePath, "чужой-секрет", string(good)),
	} {
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, req)
		if rec.Code != 401 {
			t.Errorf("без верной подписи ждали 401, получили %d", rec.Code)
		}
	}
	// мусор вместо точки: не base64, не точка на кривой, нулевая точка
	for _, body := range []string{`{"blinded":"!!!"}`, `{"blinded":"AAAA"}`, string(good), `не json`} {
		rec := httptest.NewRecorder()
		mux.ServeHTTP(rec, svc.SignedRequest("POST", EvaluatePath, secret, body))
		if rec.Code != 400 {
			t.Errorf("тело %q: ждали 400, получили %d", body, rec.Code)
		}
	}
	// слишком большое тело
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, svc.SignedRequest("POST", EvaluatePath, secret, string(make([]byte, svc.MaxBodyBytes+1))))
	if rec.Code != 413 {
		t.Errorf("большое тело: ждали 413, получили %d", rec.Code)
	}
}

func TestPublicKeyIsSignedAndStable(t *testing.T) {
	mux, tk := newServer(t)
	rec := httptest.NewRecorder()
	mux.ServeHTTP(rec, httptest.NewRequest("GET", PublicKeyPath, nil)) // запрос без подписи
	if rec.Code != 200 {
		t.Fatalf("public-key: %d", rec.Code)
	}
	if !signing.Verify(secret, rec.Body.Bytes(), rec.Header().Get(signing.SignatureHeader),
		rec.Header().Get(signing.TimestampHeader), time.Now()) {
		t.Error("ответ public-key не подписан")
	}
	var resp map[string]string
	_ = json.Unmarshal(rec.Body.Bytes(), &resp)
	if resp["suite"] != "P256-SHA256" || resp["public_key"] != base64.StdEncoding.EncodeToString(tk.PublicKey()) {
		t.Errorf("неверный ответ: %v", resp)
	}
	if len(tk.PublicKey()) != 33 {
		t.Errorf("открытый ключ P-256 в сжатом виде — 33 байта, а не %d", len(tk.PublicKey()))
	}
}

func TestKeyFileCreatedOnceWithPrivatePerms(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sub", "oprf.key")
	first, err := LoadKey("", path)
	if err != nil {
		t.Fatal(err)
	}
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if perm := info.Mode().Perm(); perm != 0o600 && os.PathSeparator == '/' {
		t.Errorf("права ключа %o, ждали 600", perm)
	}
	second, err := LoadKey("", path)
	if err != nil || !bytes.Equal(first, second) {
		t.Error("второй старт должен читать тот же ключ, а не создавать новый")
	}
	// ключ из окружения главнее файла
	other := make([]byte, 32)
	_, _ = rand.Read(other)
	fromEnv, err := LoadKey(base64.StdEncoding.EncodeToString(other), path)
	if err != nil || !bytes.Equal(fromEnv, other) {
		t.Error("OPRF_PRIVATE_KEY должен иметь приоритет")
	}
	if _, err := LoadKey("не base64 !!!", path); err == nil {
		t.Error("битый ключ из окружения должен давать ошибку")
	}
	if _, err := New([]byte("слишком короткий")); err == nil {
		t.Error("ключ неверной длины должен отвергаться")
	}
}
