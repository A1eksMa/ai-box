# Stage 4 — FastAPI web terminal

## Цель

Реализовать WebSocket-прокси между браузером и tmux-сессией: FastAPI открывает PTY, запускает в нём `tmux attach-session`, и прозрачно передаёт байты в обе стороны.

## app.py

```python
import asyncio
import fcntl
import os
import pty
import struct
import termios

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

ROOT         = os.environ.get("ROOT_PATH", "")
WEB_TOKEN    = os.environ["WEB_TOKEN"]
TMUX_SESSION = os.environ.get("TMUX_SESSION", "main")
PTY_ROWS     = int(os.environ.get("PTY_ROWS", "60"))
PTY_COLS     = int(os.environ.get("PTY_COLS", "220"))

app = FastAPI()
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")


def authenticated(request: Request) -> bool:
    return request.cookies.get("auth_token") == WEB_TOKEN


@app.get("/")
async def index(request: Request):
    if not authenticated(request):
        return RedirectResponse(f"{ROOT}/login")
    return templates.TemplateResponse(request, "index.html")


@app.websocket("/ws")
async def terminal(ws: WebSocket):
    token = ws.cookies.get("auth_token") or ws.query_params.get("token")
    if token != WEB_TOKEN:
        await ws.close(code=4401)
        return
    await ws.accept()

    master_fd, slave_fd = pty.openpty()
    # Инициализируем PTY теми же размерами, что и tmux-сессия,
    # чтобы attach не сжал её до дефолтных 80×24
    fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                struct.pack("HHHH", PTY_ROWS, PTY_COLS, 0, 0))
    proc = await asyncio.create_subprocess_exec(
        "tmux", "attach-session", "-t", TMUX_SESSION,
        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        close_fds=True,
    )
    os.close(slave_fd)

    async def read_from_pty():
        loop = asyncio.get_running_loop()
        while True:
            try:
                data = await loop.run_in_executor(None, os.read, master_fd, 4096)
                await ws.send_bytes(data)
            except OSError:
                break

    async def write_to_pty():
        while True:
            try:
                msg = await ws.receive()
                if "bytes" in msg:
                    data = msg["bytes"]
                    # resize-сообщение: первый байт 0x01, затем rows(2), cols(2)
                    if len(data) == 5 and data[0] == 0x01:
                        rows = int.from_bytes(data[1:3], "big")
                        cols = int.from_bytes(data[3:5], "big")
                        fcntl.ioctl(master_fd, termios.TIOCSWINSZ,
                                    struct.pack("HHHH", rows, cols, 0, 0))
                    else:
                        os.write(master_fd, data)
                elif "text" in msg:
                    os.write(master_fd, msg["text"].encode())
            except WebSocketDisconnect:
                break

    try:
        await asyncio.gather(read_from_pty(), write_to_pty())
    finally:
        proc.terminate()
        try:
            os.close(master_fd)
        except OSError:
            pass
```

## Фронтенд (изменения в index.html)

Минимальные правки к существующему `templates/index.html`:

1. Отправлять данные как `binary` (bytes), не text
2. Отправлять resize-сообщение при изменении размера окна:

```javascript
// resize
term.onResize(({ rows, cols }) => {
    const buf = new Uint8Array(5);
    buf[0] = 0x01;
    new DataView(buf.buffer).setUint16(1, rows, false);
    new DataView(buf.buffer).setUint16(3, cols, false);
    socket.send(buf);
});

// ввод — бинарно
term.onData(data => {
    socket.send(new TextEncoder().encode(data));
});

// вывод — бинарно
socket.onmessage = (event) => {
    if (event.data instanceof Blob) {
        event.data.arrayBuffer().then(buf => term.write(new Uint8Array(buf)));
    } else {
        term.write(event.data);
    }
};
```

## Поведение при множественных подключениях

Каждый WebSocket-клиент делает свой `tmux attach`. tmux корректно обрабатывает несколько клиентов на одной сессии — все видят одинаковый экран. Размер окна определяется наименьшим из подключённых клиентов (стандартное поведение tmux).
