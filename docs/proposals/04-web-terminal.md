# Stage 4 — FastAPI web terminal

## Цель

Адаптировать существующий `app.py` (Flask + gevent) под новую задачу: вместо `docker exec bash` подключаться к tmux-сессии `main` внутри того же контейнера.

## Ключевое отличие от текущего app.py

| | Текущий MVP | AI Box |
|---|---|---|
| Бэкенд | Flask + gevent | FastAPI + uvicorn |
| Что открывается | `docker exec bash` в отдельном контейнере | `tmux attach-session -t main` внутри того же контейнера |
| Сессий | новая на каждое подключение | одна общая, все клиенты видят одно |
| Resize | нет | да (через WebSocket message) |

## app.py (новый)

```python
import asyncio
import os
import struct
import fcntl
import termios
import pty
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI()

@app.get("/")
async def index():
    with open("templates/index.html") as f:
        return HTMLResponse(f.read())

@app.websocket("/ws")
async def terminal(ws: WebSocket):
    await ws.accept()

    # Открываем PTY и запускаем tmux attach
    master_fd, slave_fd = pty.openpty()
    proc = await asyncio.create_subprocess_exec(
        "tmux", "attach-session", "-t", "main",
        stdin=slave_fd, stdout=slave_fd, stderr=slave_fd,
        close_fds=True
    )
    os.close(slave_fd)

    async def read_from_pty():
        loop = asyncio.get_event_loop()
        while True:
            try:
                data = await loop.run_in_executor(None, os.read, master_fd, 1024)
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
                    if data[0] == 0x01:
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

    await asyncio.gather(read_from_pty(), write_to_pty())
    proc.terminate()
    os.close(master_fd)
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
