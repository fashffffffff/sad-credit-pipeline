package svc

import (
	"fmt"
	"log/slog"
	"os"
	"strings"
)

// Публичные значения по умолчанию из кода: они лежат в репозитории, поэтому секретом не считаются.
var knownDefaults = map[string]bool{
	"dev-jwt-secret-change-me": true,
	"dev-smev-secret":          true,
	"dev-bki-secret":           true,
	"dev-tokenizer-secret":     true,
	"dev-pii-pepper":           true,
}

const minLengthProd = 32 // HMAC-SHA256 просит ключ не короче хэша (256 бит)

// IsProduction: APP_ENV=production.
func IsProduction() bool {
	env := strings.ToLower(strings.TrimSpace(os.Getenv("APP_ENV")))
	return env == "production"
}

// CheckSecret — как common/secrets_guard.py: пустой секрет запрещён всегда,
// демонстрационный или короткий — только в проде (в разработке громкое предупреждение).
func CheckSecret(name, value string, production bool) error {
	if value == "" {
		return fmt.Errorf("%s пуст: задайте длинный случайный секрет (например, openssl rand -hex 32)", name)
	}
	if !knownDefaults[value] && len(value) >= minLengthProd {
		return nil
	}
	if production {
		return fmt.Errorf("%s: слабый или демонстрационный секрет, а APP_ENV=production. "+
			"Задайте случайное значение от %d символов (openssl rand -hex 32)", name, minLengthProd)
	}
	slog.Warn("ВНИМАНИЕ: демонстрационный/короткий секрет. Для демо можно, в проде нельзя "+
		"(с APP_ENV=production сервис не стартует)", "secret", name)
	return nil
}

// SecretFromEnv читает секрет из окружения (или берёт демо-значение) и проверяет его.
func SecretFromEnv(name, def string) (string, error) {
	value, ok := os.LookupEnv(name)
	if !ok {
		value = def
	}
	if err := CheckSecret(name, value, IsProduction()); err != nil {
		return "", err
	}
	return value, nil
}
