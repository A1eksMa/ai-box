# Stage 7 — Резервный вход при блокировке сети (mirror + relay)

## Проблема

`ai-box` разворачивается на одном сервере с одним доменом. Если сеть, из которой заходит пользователь, блокирует этот конкретный домен/IP (провайдерская фильтрация, DPI, блэклист по репутации адреса и т.п.), сервис становится недоступен целиком — несмотря на то что сам сервер жив и работает.

Хочется иметь второй, независимый адрес входа, который:
- отдаёт тот же терминал, ту же tmux-сессию, что и основной домен;
- не требует второй копии `ai-box` (второй `claude`-сессии, второй авторизации, разъезжающихся данных);
- со стороны не выглядит как «прокси на другой сервер» — в идеале как самостоятельный сервис.

## Идея

Два инстанса:

- **backend** — сервер, на котором развёрнут `ai-box` по схеме из Stage 1–6 (домен `backend.example`).
- **edge** — второй, независимый сервер с отдельным доменом (`edge.example`), который публично отдаёт ту же страницу, ничего не зная о содержимом сам — весь трафик прозрачно уходит на backend.

Соединение между инстансами — через [Xray](https://github.com/XTLS/Xray-core) (VLESS поверх gRPC+TLS). С точки зрения внешнего наблюдателя (DPI, анализ трафика) это выглядит как обычный HTTPS-вызов к gRPC-микросервису на поддомене — а не как прозрачное проксирование одного сайта на другой.

## Архитектура

```
Клиент ──HTTPS──▶ nginx @ edge (edge.example:443, свой сертификат)
                       │ proxy_pass → 127.0.0.1:10085
                       ▼
              Xray "portal" @ edge
                 ├─ 127.0.0.1:10085  (принимает от nginx обычный HTTP/WS)
                 └─ 0.0.0.0:8443     (VLESS + gRPC + TLS, домен grpc.edge.example)
                       ▲
                       │ туннель, обе стороны данных текут по нему после установки
              Xray "bridge" @ backend
                       │ freedom outbound → 127.0.0.1:8000
                       ▼
                    ai-box (uvicorn, из Stage 4)
```

`backend.example` продолжает работать напрямую (см. Stage 4–6) — эта схема ничего в нём не меняет, только добавляет ещё один вход.

### Почему туннель инициирует backend, а не edge

Естественная логика — «edge как прокси должен сам обращаться к backend». На практике направление соединения между двумя конкретными серверами может быть заблокировано только в одну сторону: типичная причина — IP одного из инстансов уже засвечен в чьих-то threat-intel базах (например, при переиспользовании облачным провайдером IP-адреса, который раньше принадлежал скомпрометированной или атакующей машине), и сеть на стороне второго инстанса фильтрует пакеты от этого адреса на уровне провайдера/апстрима — ещё до файрвола самой машины.

Xray поддерживает ровно этот сценарий — функцию `reverse` (роли `bridge` и `portal`), изначально придуманную для NAT-трейверсала: машина с реальным сервисом сама дозванивается наружу и держит канал открытым, а внешняя точка входа заворачивает через уже установленный канал публичный трафик. Направление, в котором устанавливается TCP/TLS-соединение, не ограничивает направление, в котором потом текут данные — поэтому не важно, что инициатором является backend, а не edge.

**Практический вывод:** прежде чем проектировать, кто к кому «должен» стучаться, проверьте связность в обе стороны (`nc -zv`, `curl` с обеих машин друг на друга). Направление может оказаться асимметричным без какой-либо ошибки конфигурации — просто из-за репутации конкретного IP на конкретный момент.

## Установка

### 0. Предварительно

- `backend` уже развёрнут по Stage 1–6, домен `backend.example` работает.
- Зарегистрирован второй сервер (`edge`), на нём проведена базовая защита: `ufw`, `fail2ban`, обновления, SSH-хардening.
- Есть домен `edge.example`, A-запись указывает на IP `edge`.
- Создан **дополнительный** поддомен `grpc.edge.example` (тоже A-запись на IP `edge`) — под него будет отдельный TLS-сертификат для маскировки под gRPC-эндпоинт. Имя поддомена произвольное, важно только чтобы оно не совпадало с основным и не подписью узла-получателя.

### 1. nginx + сертификаты на edge

```bash
apt-get install -y nginx certbot python3-certbot-nginx

# основной сайт — публичный вход, форвардит на локальный порт тоннеля
cat > /etc/nginx/sites-available/edge.example <<'CONF'
server {
    listen 80;
    server_name edge.example;
    location / {
        proxy_pass http://127.0.0.1:10085;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
CONF

# поддомен под туннель — только ACME challenge, Xray сам терминирует TLS для него
cat > /etc/nginx/sites-available/grpc.edge.example <<'CONF'
server {
    listen 80;
    server_name grpc.edge.example;
    location /.well-known/acme-challenge/ { root /var/www/html; }
    location / { return 404; }
}
CONF

ln -sf /etc/nginx/sites-available/edge.example /etc/nginx/sites-enabled/
ln -sf /etc/nginx/sites-available/grpc.edge.example /etc/nginx/sites-enabled/
mkdir -p /var/www/html
nginx -t && systemctl reload nginx

certbot --nginx -d edge.example --agree-tos --non-interactive --redirect
certbot certonly --nginx -d grpc.edge.example --agree-tos --non-interactive
```

### 2. ufw на edge

```bash
ufw allow 8443/tcp comment 'Xray interconn'
```

**Не забыть этот шаг** — см. «Проблемы» ниже, это одна из двух причин, по которым туннель не поднимается с первого раза.

### 3. Установка Xray на обоих инстансах

Официальный установщик проекта:

```bash
bash -c "$(curl -L https://github.com/XTLS/Xray-install/raw/main/install-release.sh)" @ install
```

### 4. Конфиг bridge (backend) — `/usr/local/etc/xray/config.json`

```json
{
  "log": { "loglevel": "warning" },
  "inbounds": [],
  "outbounds": [
    {
      "tag": "out",
      "protocol": "freedom",
      "settings": {
        "redirect": "127.0.0.1:8000",
        "finalRules": [
          { "action": "allow", "network": "tcp", "port": 8000, "ip": ["127.0.0.1/32"] },
          { "action": "block" }
        ]
      }
    },
    {
      "tag": "interconn",
      "protocol": "vless",
      "settings": {
        "vnext": [
          {
            "address": "grpc.edge.example",
            "port": 8443,
            "users": [
              { "id": "<SHARED-UUID>", "encryption": "none" }
            ]
          }
        ]
      },
      "streamSettings": {
        "network": "grpc",
        "security": "tls",
        "grpcSettings": { "serviceName": "<service-name>" },
        "tlsSettings": { "serverName": "grpc.edge.example" }
      }
    }
  ],
  "reverse": {
    "bridges": [
      { "tag": "bridge", "domain": "reverse-proxy.xray.internal" }
    ]
  },
  "routing": {
    "rules": [
      {
        "type": "field",
        "inboundTag": ["bridge"],
        "domain": ["full:reverse-proxy.xray.internal"],
        "outboundTag": "interconn"
      },
      { "type": "field", "inboundTag": ["bridge"], "outboundTag": "out" }
    ]
  }
}
```

`127.0.0.1:8000` — порт uvicorn из docker-compose.yml (`"127.0.0.1:8000:8080"`), тот же, что слушает nginx на backend напрямую. Порт TLS-соединения с edge (`8443`) и внутренний routing-домен (`reverse-proxy.xray.internal`, чисто служебный токен, не настоящий DNS) должны совпадать между конфигами bridge и portal.

### 5. Конфиг portal (edge) — `/usr/local/etc/xray/config.json`

```json
{
  "log": { "loglevel": "warning" },
  "inbounds": [
    {
      "tag": "external",
      "listen": "127.0.0.1",
      "port": 10085,
      "protocol": "tunnel",
      "settings": {
        "allowedNetwork": "tcp",
        "rewriteAddress": "127.0.0.1",
        "rewritePort": 80
      }
    },
    {
      "tag": "interconn",
      "listen": "0.0.0.0",
      "port": 8443,
      "protocol": "vless",
      "settings": {
        "clients": [ { "id": "<SHARED-UUID>" } ],
        "decryption": "none"
      },
      "streamSettings": {
        "network": "grpc",
        "security": "tls",
        "grpcSettings": { "serviceName": "<service-name>" },
        "tlsSettings": {
          "certificates": [
            {
              "certificateFile": "/etc/letsencrypt/live/grpc.edge.example/fullchain.pem",
              "keyFile": "/etc/letsencrypt/live/grpc.edge.example/privkey.pem"
            }
          ]
        }
      }
    }
  ],
  "outbounds": [
    { "tag": "portal-out", "protocol": "freedom" }
  ],
  "reverse": {
    "portals": [
      { "tag": "portal", "domain": "reverse-proxy.xray.internal" }
    ]
  },
  "routing": {
    "rules": [
      { "type": "field", "inboundTag": ["external"], "outboundTag": "portal" },
      { "type": "field", "inboundTag": ["interconn"], "outboundTag": "portal" }
    ]
  }
}
```

`<SHARED-UUID>` — общий секрет (генерируется один раз, например `python3 -c "import uuid; print(uuid.uuid4())"`), должен совпадать в обоих конфигах. `<service-name>` — произвольная строка в стиле имени gRPC-сервиса (например `InternalSyncService`), тоже должна совпадать.

### 6. Права на сертификаты (backend не касается, только edge)

Официальный systemd-юнit Xray запускает процесс от пользователя `nobody`. Сертификаты Let's Encrypt по умолчанию читаемы только root'ом — без явного разрешения Xray не сможет их прочитать (см. «Проблемы»).

```bash
apt-get install -y acl
setfacl -R -m u:nobody:rX /etc/letsencrypt/live /etc/letsencrypt/archive
setfacl -R -d -m u:nobody:rX /etc/letsencrypt/live /etc/letsencrypt/archive
```

Второй `setfacl` (с `-d`, default ACL) нужен, чтобы разрешение автоматически применялось и к файлам, которые certbot создаст при следующем продлении сертификата.

### 7. Запуск

Порядок: сначала bridge (backend), потом portal (edge) — так рекомендует официальная документация Xray.

```bash
# backend
systemctl enable --now xray

# edge
systemctl enable --now xray
```

### 8. Проверка

```bash
curl -sS -o /dev/null -w "HTTP %{http_code}\n" https://edge.example/login
```

Ожидается `HTTP 200` и содержимое, идентичное `https://backend.example/login`.

## Проблемы, с которыми столкнулись, и их решения

### 1. Направление связи между инстансами оказалось асимметрично заблокировано

**Симптом:** `curl`/`nc` с edge на backend по любому порту (включая 22 — просто для диагностики) висли ~2 минуты и обрывались таймаутом; при этом обратное направление (backend → edge) работало нормально.

**Диагностика:** пустой `ufw.log` на backend по IP edge (пакеты не долетают даже до файрвола сервера — значит режутся выше, на сети/у провайдера), в сочетании с аномально высоким числом сканов/атак на свежий IP edge в первые же минуты его жизни — верный признак «грязного» переиспользованного облачного адреса, уже попавшего в чьи-то репутационные списки.

**Решение:** не пытаться починить сетевую фильтрацию (это не в зоне контроля приложения — либо смена IP у провайдера, либо тикет в поддержку, результат не гарантирован), а развернуть Xray reverse-proxy в направлении, которое реально работает: backend инициирует соединение к edge, а не наоборот (см. «Архитектура» выше).

### 2. Xray на portal не открывал ни одного порта, хотя `systemctl status` показывал `active`

**Симптом:** `curl` на `edge.example` — 502 от nginx; TLS-хендшейк на `grpc.edge.example:8443` не проходит вообще; при этом `systemctl status xray` — `active (running)`, в логе `warning`-уровня — ни одной ошибки.

**Причина:** systemd-юнит официального инсталлятора запускает Xray от `User=nobody`; сертификаты Let's Encrypt (`/etc/letsencrypt/{live,archive}/...`) читаемы только root'ом. При недостаточном уровне логирования (`warning`) фатальная ошибка инициализации TLS-инбаунда не попадала в журнал — процесс считался успешно запущенным, но фактически не создавал ни одного слушателя.

**Диагностика:** временно поднять `"loglevel": "debug"` и перезапустить сервис — в логе сразу видна причина: `open /etc/letsencrypt/live/.../fullchain.pem: permission denied`.

**Решение:** см. шаг 6 выше (ACL для `nobody`).

### 3. После исправления сертификатов — `empty worker list` на portal / `i/o timeout` на bridge

**Симптом:** portal теперь слушает оба порта, но при попытке подключиться в лог падает `app/reverse: failed to process reverse connection > app/reverse: empty worker list`; на стороне bridge — постоянные `dial tcp <edge-ip>:8443: i/o timeout`.

**Причина:** банальная — при первичной настройке `ufw` на edge был разрешён только тот набор портов, что нужен был на тот момент (`22/80/443`); порт `8443` под ещё не существовавший Xray-туннель добавлен не был. Файрвол молча дропал входящие пакеты бриджа.

**Решение:** `ufw allow 8443/tcp` на edge (шаг 2 выше). После этого туннель поднимается сразу.

## Диагностика: чек-лист на будущее

Если после установки `curl https://edge.example/...` не работает:

1. `systemctl status xray` на обеих машинах — `active`, но само по себе ничего не гарантирует (см. проблему №2).
2. Временно `"loglevel": "debug"` в обоих конфигах, `systemctl restart xray`, смотреть `journalctl -u xray -f`.
3. `ss -tulnp | grep xray` на portal — должны быть заняты оба порта (локальный tunnel-порт и публичный TLS-порт). Если пусто — почти наверняка проблема с правами на сертификат.
4. `nc -zv <edge-ip> 8443` с backend — если висит на таймауте, проверить `ufw status` на edge.
5. В логе portal ищите `empty worker list` (бридж не подключился) и в логе bridge — `i/o timeout` / `connection refused` (сетевая проблема) vs TLS-ошибки (проблема с сертификатом/доменом).
6. Не забыть вернуть `"loglevel": "warning"` после отладки.

## Ограничения

- Xray 26.x предупреждает, что gRPC-транспорт для VLESS считается deprecated в пользу XHTTP (`Please migrate to XHTTP stream-up H2 as soon as possible`). Работает и так, но при обновлении Xray стоит проверить актуальность.
- Схема добавляет дополнительный сетевой хоп → на 1-2 секунды больше задержка по сравнению с прямым доступом к backend.
- `edge` не хранит и не обрабатывает никаких данных сам — компрометация edge не даёт прямого доступа к `ai-box`/`.claude` на backend, но даёт возможность подменять/перехватывать трафик пользователей, идущий через него (как у любого reverse-proxy).
