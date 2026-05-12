# Stage 5 — Авторизация

## Цель

Защитить веб-терминал от несанкционированного доступа. Один пользователь, без БД, токен хранится в `.env` на хосте.

## Схема

При открытии страницы браузер показывает форму ввода токена. Токен передаётся как query-параметр при установке WebSocket-соединения. FastAPI middleware проверяет его до accept(). Если токен неверный — соединение закрывается с кодом 4401.

Для HTTP-маршрута `/` — cookie `auth_token`. Если cookie нет или неверная — редирект на `/login`.

## Реализация в app.py

```python
import os
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse

WEB_TOKEN = os.environ["WEB_TOKEN"]  # обязательно, без дефолта

# --- HTTP auth ---

@app.get("/login")
async def login_page():
    with open("templates/login.html") as f:
        return HTMLResponse(f.read())

@app.post("/login")
async def login(request: Request):
    form = await request.form()
    if form.get("token") == WEB_TOKEN:
        response = RedirectResponse("/", status_code=302)
        response.set_cookie("auth_token", WEB_TOKEN, httponly=True, samesite="strict")
        return response
    return RedirectResponse("/login?error=1", status_code=302)

@app.get("/")
async def index(request: Request):
    if request.cookies.get("auth_token") != WEB_TOKEN:
        return RedirectResponse("/login")
    with open("templates/index.html") as f:
        return HTMLResponse(f.read())

# --- WebSocket auth ---

@app.websocket("/ws")
async def terminal(ws: WebSocket):
    token = ws.cookies.get("auth_token") or ws.query_params.get("token")
    if token != WEB_TOKEN:
        await ws.close(code=4401)
        return
    await ws.accept()
    # ... остальной код из Stage 4
```

## login.html (минимальный)

```html
<!doctype html>
<html>
<head><title>Login</title></head>
<body style="background:#000; color:#0f0; font-family:monospace; display:flex; height:100vh; align-items:center; justify-content:center;">
  <form method="POST" action="/login">
    <input type="password" name="token" placeholder="Access token" autofocus
           style="background:#111; color:#0f0; border:1px solid #0f0; padding:8px; font-size:16px;">
    <button type="submit" style="background:#0f0; color:#000; border:none; padding:8px 16px; margin-left:8px; cursor:pointer;">Enter</button>
  </form>
</body>
</html>
```

## Безопасность

- Токен генерируется через `openssl rand -hex 32` — достаточная энтропия
- `httponly=True` — токен недоступен из JavaScript
- HTTPS обязателен (nginx с SSL, уже есть на инстансе) — без него cookie передаётся в открытом виде
- Добавить `secure=True` к `set_cookie` при работе через HTTPS

## Что НЕ реализуется намеренно

- Rate limiting на `/login` — однопользовательский режим, токен длинный
- Refresh/expire токенов — усложнение без реальной пользы
- CSRF-защита — форма не меняет состояние сервера, только устанавливает cookie
