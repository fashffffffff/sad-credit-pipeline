// Package emulation — формулы эмуляторов БКИ и СМЭВ из спецификации организаторов (06_external_services.md).
// Зеркало Python-модуля credit_core/emulation.py; совпадение проверяет тест на общих векторах.
package emulation

import (
	"crypto/md5"
	"math/big"
)

// hash возвращает md5(value) как большое число (int(md5, 16) в Python).
func hash(value string) *big.Int {
	sum := md5.Sum([]byte(value))
	return new(big.Int).SetBytes(sum[:])
}

// BkiScore — ответ /bki/score.
type BkiScore struct {
	Score       int  `json:"score"`
	Delinquency bool `json:"delinquency"`
	Requests90d int  `json:"requests_90d"`
}

// Score считает скор БКИ по passport_hash.
// h = md5 % 10: h ≤ 5 → 600+20h; 6..8 → 450+50(h−6); 9 → 400 и просрочка.
func Score(passportHash string) BkiScore {
	h := int(new(big.Int).Mod(hash(passportHash), big.NewInt(10)).Int64())
	switch {
	case h <= 5:
		return BkiScore{Score: 600 + h*20, Requests90d: 1}
	case h <= 8:
		return BkiScore{Score: 450 + (h-6)*50, Requests90d: 1}
	default:
		return BkiScore{Score: 400, Delinquency: true, Requests90d: 1}
	}
}

// Корзины историй для /bki/report.
const (
	Good   = "good"
	Medium = "medium"
	Bad    = "bad"
)

// Bucket возвращает корзину и «большой индекс» md5 // 10; индекс в корзине = он % len(корзины).
// Корзина выбирается тем же h, что и скор, чтобы они не противоречили друг другу.
func Bucket(passportHash string) (bucket string, index *big.Int) {
	big10 := big.NewInt(10)
	q, m := new(big.Int).DivMod(hash(passportHash), big10, new(big.Int))
	switch h := m.Int64(); {
	case h <= 5:
		bucket = Good
	case h <= 8:
		bucket = Medium
	default:
		bucket = Bad
	}
	return bucket, q
}

// IndexIn — номер истории в корзине из n историй.
func IndexIn(index *big.Int, n int) int {
	return int(new(big.Int).Mod(index, big.NewInt(int64(n))).Int64())
}

// SmevStatus: h = md5(client_id) % 10; h == 0 → MISMATCH (1 из 10), иначе MATCH.
func SmevStatus(clientID string) string {
	if new(big.Int).Mod(hash(clientID), big.NewInt(10)).Sign() == 0 {
		return "MISMATCH"
	}
	return "MATCH"
}
