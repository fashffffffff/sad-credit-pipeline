package svc

import "testing"

func TestCheckSecret(t *testing.T) {
	long := "0123456789abcdef0123456789abcdef"
	cases := []struct {
		name, value string
		prod, fail  bool
	}{
		{"пустой в разработке", "", false, true},
		{"пустой в проде", "", true, true},
		{"демо в разработке", "dev-bki-secret", false, false},
		{"демо в проде", "dev-bki-secret", true, true},
		{"короткий в проде", "short", true, true},
		{"длинный в проде", long, true, false},
		{"длинный в разработке", long, false, false},
	}
	for _, c := range cases {
		if err := CheckSecret("X", c.value, c.prod); (err != nil) != c.fail {
			t.Errorf("%s: err=%v, ждали отказ=%v", c.name, err, c.fail)
		}
	}
}

func TestSecretFromEnv(t *testing.T) {
	t.Setenv("APP_ENV", "production")
	t.Setenv("TEST_SECRET", "")
	if _, err := SecretFromEnv("TEST_SECRET", "dev-bki-secret"); err == nil {
		t.Error("пустое значение в окружении должно давать отказ (а не подмену демо-значением)")
	}
	t.Setenv("APP_ENV", "development")
	t.Setenv("TEST_SECRET", "")
	if _, err := SecretFromEnv("TEST_SECRET", "dev-bki-secret"); err == nil {
		t.Error("пустой секрет запрещён всегда")
	}
}

func TestShort(t *testing.T) {
	if Short("a1b2c3d4e5f6a7b8") != "a1b2c3" || Short("abc") != "abc" {
		t.Error("Short должен обрезать до 6 символов")
	}
}
