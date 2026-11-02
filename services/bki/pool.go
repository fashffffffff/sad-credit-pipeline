// Package bki — пул кредитных историй для POST /bki/report.
// Пул готовит ml/export_bki_pool.py и кладёт в data/bki_pool.json.gz; здесь он вшивается в программу через embed.
package bki

import (
	"bytes"
	"compress/gzip"
	_ "embed"
	"encoding/json"
	"fmt"
	"io"

	"sad/services/internal/emulation"
)

//go:embed data/bki_pool.json.gz
var embeddedPool []byte

// Pool — истории по корзинам. История — готовый JSON-массив кредитов: отдаём как есть, не разбирая поля.
type Pool struct {
	buckets map[string][]json.RawMessage
}

// LoadEmbedded разбирает вшитый пул.
func LoadEmbedded() (*Pool, error) { return Load(embeddedPool) }

// Load разбирает gzip-JSON вида {"good": [история, ...], "medium": [...], "bad": [...]}.
func Load(gz []byte) (*Pool, error) {
	zr, err := gzip.NewReader(bytes.NewReader(gz))
	if err != nil {
		return nil, fmt.Errorf("пул БКИ: %w", err)
	}
	raw, err := io.ReadAll(zr)
	if err != nil {
		return nil, fmt.Errorf("пул БКИ: %w", err)
	}
	p := &Pool{buckets: map[string][]json.RawMessage{}}
	if err := json.Unmarshal(raw, &p.buckets); err != nil {
		return nil, fmt.Errorf("пул БКИ: %w", err)
	}
	for _, name := range []string{emulation.Good, emulation.Medium, emulation.Bad} {
		if len(p.buckets[name]) == 0 {
			return nil, fmt.Errorf("пул БКИ: корзина %q пуста", name)
		}
	}
	return p, nil
}

// Size — сколько историй в корзине.
func (p *Pool) Size(bucket string) int { return len(p.buckets[bucket]) }

// History выбирает историю для паспортного хэша: корзина и номер в ней считаются из md5 (см. контракт, раздел 3).
func (p *Pool) History(passportHash string) json.RawMessage {
	bucket, index := emulation.Bucket(passportHash)
	histories := p.buckets[bucket]
	return histories[emulation.IndexIn(index, len(histories))]
}
