// Package tokenizer — серверная часть OPRF (RFC 9497, набор P256-SHA256, режим verifiable).
//
// Токенизатор паспорта — единственный сервис, у которого есть секретный ключ OPRF.
// Он получает «ослеплённое» число, применяет ключ и возвращает результат с доказательством (DLEQ),
// что применён именно тот ключ, чей открытый ключ опубликован. Номера паспорта он не видит.
package tokenizer

import (
	"crypto/rand"
	"encoding/base64"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/cloudflare/circl/oprf"
	"github.com/cloudflare/circl/zk/dleq"
)

// Пути API.
const (
	SuiteName     = "P256-SHA256"
	EvaluatePath  = "/oprf/v1/evaluate"
	PublicKeyPath = "/oprf/v1/public-key"
)

var suite = oprf.SuiteP256

// Tokenizer хранит ключ и умеет вычислять OPRF.
type Tokenizer struct {
	server    oprf.VerifiableServer
	publicKey []byte
}

// New собирает токенизатор из приватного ключа (сырые байты скаляра).
func New(privateKey []byte) (*Tokenizer, error) {
	var key oprf.PrivateKey
	if err := key.UnmarshalBinary(suite, privateKey); err != nil {
		return nil, fmt.Errorf("OPRF-ключ: %w", err)
	}
	pub, err := key.Public().MarshalBinary()
	if err != nil {
		return nil, err
	}
	return &Tokenizer{server: oprf.NewVerifiableServer(suite, &key), publicKey: pub}, nil
}

// PublicKey — открытый ключ (сжатая точка, 33 байта).
func (t *Tokenizer) PublicKey() []byte { return t.publicKey }

// Evaluate принимает ослеплённую точку (33 байта) и возвращает вычисленную точку и доказательство.
func (t *Tokenizer) Evaluate(blinded []byte) (evaluated, proof []byte, err error) {
	element := suite.Group().NewElement()
	if err := element.UnmarshalBinary(blinded); err != nil {
		return nil, nil, fmt.Errorf("blinded: %w", err)
	}
	if element.IsIdentity() {
		return nil, nil, errors.New("blinded: нулевая точка")
	}
	res, err := t.server.Evaluate(&oprf.EvaluationRequest{Elements: []oprf.Blinded{element}})
	if err != nil {
		return nil, nil, err
	}
	evaluated, err = res.Elements[0].MarshalBinaryCompress()
	if err != nil {
		return nil, nil, err
	}
	proof, err = marshalProof(res.Proof)
	return evaluated, proof, err
}

func marshalProof(p *dleq.Proof) ([]byte, error) { return p.MarshalBinary() }

// LoadKey берёт ключ из OPRF_PRIVATE_KEY (base64) или из файла; если файла нет — создаёт новый (права 0600).
// Возвращает сырые байты ключа.
func LoadKey(envValue, path string) ([]byte, error) {
	if strings.TrimSpace(envValue) != "" {
		return decodeKey(envValue)
	}
	data, err := os.ReadFile(path)
	if err == nil {
		return decodeKey(string(data))
	}
	if !errors.Is(err, os.ErrNotExist) {
		return nil, err
	}
	// первый старт: генерируем ключ и сохраняем
	key, err := oprf.GenerateKey(suite, rand.Reader)
	if err != nil {
		return nil, err
	}
	raw, err := key.MarshalBinary()
	if err != nil {
		return nil, err
	}
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		return nil, err
	}
	// O_EXCL: если два процесса стартуют одновременно, второй не затрёт ключ первого
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		if errors.Is(err, os.ErrExist) {
			return LoadKey("", path)
		}
		return nil, err
	}
	defer f.Close()
	if _, err := f.WriteString(base64.StdEncoding.EncodeToString(raw) + "\n"); err != nil {
		return nil, err
	}
	return raw, nil
}

func decodeKey(text string) ([]byte, error) {
	raw, err := base64.StdEncoding.DecodeString(strings.TrimSpace(text))
	if err != nil {
		return nil, fmt.Errorf("OPRF-ключ должен быть в base64: %w", err)
	}
	return raw, nil
}
