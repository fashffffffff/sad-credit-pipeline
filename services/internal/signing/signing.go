// Package signing — HMAC-подпись сообщений между банком и внешними системами.
//
// Это зеркало Python-модуля common/signing.py: подпись должна совпадать побайтно,
// иначе backend и Go-сервисы перестанут понимать друг друга.
//
// Идея: у банка и у сервиса есть общий секрет. Отправитель считает «печать»
// HMAC-SHA256(секрет, "<время>.<тело>") и кладёт её в заголовки; получатель считает печать сам и сравнивает.
// Время в подписи защищает от повтора старого сообщения (replay-атаки).
package signing

import (
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"net/http"
	"strconv"
	"time"
)

const (
	SignatureHeader = "X-Signature"
	TimestampHeader = "X-Timestamp"
	MaxAge          = 300 * time.Second // окно, в которое сообщение считается свежим
)

// ErrEmptySecret — HMAC с пустым ключом может посчитать кто угодно.
var ErrEmptySecret = errors.New("пустой секрет подписи")

// Sign возвращает hex-подпись для тела и метки времени (секунды Unix).
func Sign(secret string, body []byte, timestamp int64) (string, error) {
	if secret == "" {
		return "", ErrEmptySecret
	}
	mac := hmac.New(sha256.New, []byte(secret))
	mac.Write([]byte(strconv.FormatInt(timestamp, 10) + "."))
	mac.Write(body)
	return hex.EncodeToString(mac.Sum(nil)), nil
}

// SetHeaders подписывает тело «сейчас» и кладёт X-Signature и X-Timestamp в заголовки ответа.
func SetHeaders(h http.Header, secret string, body []byte) error {
	ts := time.Now().Unix()
	sig, err := Sign(secret, body, ts)
	if err != nil {
		return err
	}
	h.Set(SignatureHeader, sig)
	h.Set(TimestampHeader, strconv.FormatInt(ts, 10))
	return nil
}

// Verify: true, если подпись верная и сообщение не старше MaxAge. now передаём явно — так легче тестировать.
func Verify(secret string, body []byte, signature, timestamp string, now time.Time) bool {
	if secret == "" || signature == "" || timestamp == "" {
		return false
	}
	ts, err := strconv.ParseInt(timestamp, 10, 64)
	if err != nil {
		return false
	}
	age := now.Unix() - ts
	if age < 0 {
		age = -age
	}
	if time.Duration(age)*time.Second > MaxAge {
		return false
	}
	expected, err := Sign(secret, body, ts)
	if err != nil {
		return false
	}
	// ConstantTimeCompare не даёт угадывать подпись по скорости ответа
	return hmac.Equal([]byte(expected), []byte(signature))
}

// VerifyRequest проверяет подпись входящего запроса по его заголовкам.
func VerifyRequest(secret string, body []byte, r *http.Request) bool {
	return Verify(secret, body, r.Header.Get(SignatureHeader), r.Header.Get(TimestampHeader), time.Now())
}
