# Stage 6 — docker-compose и деплой

## Цель

Собрать всё воедино: контейнер стартует автоматически при загрузке инстанса, volume смонтирован, секреты загружены из `.env`.

## docker-compose.yml

```yaml
services:
  ai-box:
    build: .
    image: ai-box
    container_name: ai-box
    restart: always
    env_file:
      - ~/ai/.env
    volumes:
      - ~/ai:/root/ai
    ports:
      - "127.0.0.1:8080:8080"   # только localhost — снаружи через nginx
    tty: true
    stdin_open: true
```

`127.0.0.1:8080` — порт доступен только с хоста, не снаружи. nginx проксирует снаружи с SSL.

## nginx (добавить в существующий конфиг)

```nginx
server {
    listen 443 ssl;
    server_name ai.practiciraptor.com;   # или отдельный домен / поддомен

    ssl_certificate     /etc/letsencrypt/live/.../fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/.../privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;   # WebSocket не таймаутит за сутки
    }
}
```

`proxy_read_timeout 86400` — критично для WebSocket. Без этого nginx закроет соединение через 60 секунд простоя.

## Запуск

```bash
# Первый раз
docker compose up -d --build

# После изменений в коде
docker compose up -d --build

# Логи
docker compose logs -f ai-box

# Зайти внутрь (если нужно вручную авторизовать Claude)
docker exec -it ai-box bash
```

## Первая авторизация Claude Code (если используется OAuth)

```bash
docker exec -it ai-box bash
claude   # пройти авторизацию через браузер один раз
# токен сохранится в /root/ai/.claude/ → на хосте в ~/ai/.claude/
exit
docker compose restart ai-box   # перезапустить, теперь entrypoint запустит claude с auth
```

При использовании `ANTHROPIC_API_KEY` этот шаг не нужен.

## Автозапуск при перезагрузке инстанса

`restart: always` в compose гарантирует автозапуск если Docker-демон запущен как systemd-сервис (по умолчанию на Ubuntu/Debian). Проверить:

```bash
systemctl is-enabled docker   # должно быть "enabled"
```

## Обновление Claude Code

При выходе новой версии:

```bash
docker compose down
docker compose up -d --build   # пересобирает образ, npm ставит latest
```

Авторизация сохраняется в volume — повторный логин не нужен.
