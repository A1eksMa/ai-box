# Stage 3 — Entrypoint и tmux

## Цель

При старте контейнера автоматически создаётся tmux-сессия с запущенным Claude Code. FastAPI стартует рядом. При перезапуске контейнера сессия создаётся заново (но существующая история в volume сохраняется).

## entrypoint.sh

```bash
#!/bin/bash
set -e

SESSION="main"

# Загружаем переменные окружения из volume
if [ -f "$HOME/.env" ]; then
    export $(grep -v '^#' "$HOME/.env" | xargs)
fi

# Создаём tmux-сессию с Claude Code (если ещё не существует)
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux new-session -d -s "$SESSION" -x 220 -y 50 "claude"
fi

# Запускаем FastAPI
exec /app/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8080
```

## Что происходит при запуске

1. Загружаются `.env` с API-ключом и web-токеном из volume
2. Создаётся detached tmux-сессия `main` с процессом `claude`
3. Запускается uvicorn — FastAPI начинает слушать порт 8080
4. Браузер подключается по WebSocket → FastAPI делает `tmux attach-session -t main`

## Поведение при перезапуске контейнера

tmux-сессия живёт внутри контейнера. При `docker restart`:
- контейнер останавливается → tmux и Claude Code завершаются
- контейнер стартует → entrypoint создаёт новую tmux-сессию, Claude Code запускается снова
- история диалога в Claude Code при этом **не сохраняется** (это ограничение Claude Code, не tmux)

При **разрыве сети** (без остановки контейнера):
- контейнер продолжает работать
- tmux-сессия жива
- браузер переподключается → `tmux attach` → диалог на месте ✓

## Размер tmux-окна

`-x 220 -y 50` — начальный размер. При подключении клиента FastAPI должен отправить resize через WebSocket (см. Stage 4).

## Права

```bash
chmod +x entrypoint.sh
```
