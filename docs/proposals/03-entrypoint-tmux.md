# Stage 3 — Entrypoint и tmux

## Цель

При старте контейнера автоматически создаётся tmux-сессия с запущенным Claude Code. FastAPI стартует рядом. При перезапуске контейнера сессия создаётся заново (но существующая история в volume сохраняется).

## entrypoint.sh

```bash
#!/bin/bash
set -e

# Загружаем дополнительные переменные окружения из volume
if [ -f "$HOME/.env" ]; then
    export $(grep -v '^#' "$HOME/.env" | xargs)
fi

SESSION="${TMUX_SESSION:-main}"
ROWS="${PTY_ROWS:-60}"
COLS="${PTY_COLS:-220}"

# Создаём tmux-сессию с Claude Code (если ещё не существует)
if ! tmux has-session -t "$SESSION" 2>/dev/null; then
    tmux new-session -d -s "$SESSION" -x "$COLS" -y "$ROWS" "cd $HOME/projects && claude"
fi

# Запускаем FastAPI
exec /app/venv/bin/uvicorn app:app --host 0.0.0.0 --port 8080
```

## Что происходит при запуске

1. Загружаются переменные окружения из `$HOME/.env` (ANTHROPIC_API_KEY и др.)
2. Читаются параметры сессии: `TMUX_SESSION` (default: `main`), `PTY_ROWS` (default: `60`), `PTY_COLS` (default: `220`)
3. Создаётся detached tmux-сессия в `$HOME/projects` с процессом `claude`
4. Запускается uvicorn — FastAPI начинает слушать порт 8080
5. Браузер подключается по WebSocket → FastAPI делает `tmux attach-session -t $SESSION`

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

`PTY_COLS x PTY_ROWS` (default: `220x60`) — начальный размер. Важно: PTY в app.py инициализируется теми же размерами перед `tmux attach`, чтобы attach не сжал сессию до дефолтных 80×24. После подключения браузер отправляет resize через WebSocket с реальным размером окна (см. Stage 4).

## Права

```bash
chmod +x entrypoint.sh
```
