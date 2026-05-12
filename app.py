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


@app.get("/login")
async def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html")


@app.post("/login")
async def login(request: Request):
    form = await request.form()
    if form.get("token") == WEB_TOKEN:
        response = RedirectResponse(f"{ROOT}/", status_code=302)
        response.set_cookie("auth_token", WEB_TOKEN, httponly=True, samesite="strict", secure=True)
        return response
    return RedirectResponse(f"{ROOT}/login?error=1", status_code=302)


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
