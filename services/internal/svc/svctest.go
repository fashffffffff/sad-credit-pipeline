package svc

import (
	"bytes"
	"net/http"
	"net/http/httptest"
	"strconv"
	"time"

	"sad/services/internal/signing"
)

// SignedRequest собирает подписанный запрос — для тестов обработчиков. Это не тестовый файл,
// чтобы им пользовались тесты других пакетов.
func SignedRequest(method, path, secret, body string) *http.Request {
	req := httptest.NewRequest(method, path, bytes.NewReader([]byte(body)))
	ts := time.Now().Unix()
	sig, _ := signing.Sign(secret, []byte(body), ts)
	req.Header.Set(signing.SignatureHeader, sig)
	req.Header.Set(signing.TimestampHeader, strconv.FormatInt(ts, 10))
	return req
}
