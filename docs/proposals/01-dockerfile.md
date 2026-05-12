# Stage 1 — Dockerfile

## Цель

Собрать Docker-образ, в котором установлены Claude Code CLI, tmux, Python и FastAPI. Образ должен быть минималистичным и воспроизводимым.

## Базовый образ

`node:22-slim` (Debian-based) — Claude Code требует Node.js. Slim-вариант даёт меньший размер при сохранении совместимости.

## Что устанавливается

| Пакет | Зачем |
|---|---|
| `@anthropic-ai/claude-code` (npm) | Claude Code CLI |
| `tmux` | Удержание сессии при разрыве связи |
| `python3`, `pip` | FastAPI backend |
| `fastapi`, `uvicorn[standard]` | Веб-сервер и WebSocket |
| `bash`, `curl`, `git` | Базовые утилиты внутри контейнера |

## Dockerfile

```dockerfile
FROM node:22-slim

# Системные зависимости
RUN apt-get update && apt-get install -y \
    tmux \
    python3 \
    python3-pip \
    python3-venv \
    bash \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Claude Code CLI
RUN npm install -g @anthropic-ai/claude-code

# Python-зависимости
WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /app/venv \
    && /app/venv/bin/pip install --no-cache-dir -r requirements.txt

# Исходники FastAPI
COPY app.py .
COPY templates/ templates/

# Домашняя папка — volume снаружи
ENV HOME=/root/ai

EXPOSE 8080

ENTRYPOINT ["/app/entrypoint.sh"]
```

## requirements.txt

```
fastapi
uvicorn[standard]
websockets
```

## Сборка

```bash
docker build -t ai-box .
```

## Примечания

- `HOME=/root/ai` — Claude Code хранит конфиг в `$HOME/.claude/`. Переопределяя HOME, мы направляем его прямо в volume.
- Entrypoint выделен в отдельный скрипт — см. [Stage 3](./03-entrypoint-tmux.md).
- `npm install -g` ставит последнюю версию Claude Code при каждом `docker build`. Для pinning-версии: `@anthropic-ai/claude-code@<version>`.
