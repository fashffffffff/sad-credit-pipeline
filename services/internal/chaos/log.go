package chaos

import "log/slog"

func slogChange(service string, s State) {
	slog.Info("хаос-режим изменён", "target", service, "mode", s.Mode, "delay_ms", s.DelayMS)
}
