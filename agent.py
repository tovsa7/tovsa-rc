#!/usr/bin/env python3
"""
Tovsa RC Agent
Локальный агент для выполнения команд от Tovsa Remote Client.

Запуск:
    python agent.py

HTTP  на localhost:7070  (для локального использования)
HTTPS на localhost:7071  (для PWA с GitHub Pages — ОБЯЗАТЕЛЬНО)

Требования: Python 3.7+, никаких зависимостей (openssl нужен для HTTPS)
"""

import json
import os
import ssl
import struct
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

PORT  = 7071
HOST  = "127.0.0.1"

INSTALL_DIR = Path.home() / ".tovsa"
CERT_FILE   = INSTALL_DIR / "cert.pem"
KEY_FILE    = INSTALL_DIR / "key.pem"


# ── Рабочая директория ─────────────────────────────────
def _default_cwd():
    termux_home = Path("/data/data/com.termux/files/home")
    if termux_home.exists():
        shared = termux_home / "storage" / "shared"
        if shared.exists():
            return str(shared)
        return str(termux_home)
    return str(Path.home())

CWD = _default_cwd()
IS_TERMUX = Path("/data/data/com.termux/files/home").exists()

# ── Screen: размер экрана ──────────────────────────────
_screen_size: tuple[int, int] | None = None

def _get_screen_size() -> tuple[int, int]:
    """Возвращает (width, height) экрана. Кешируется."""
    global _screen_size
    if _screen_size:
        return _screen_size
    try:
        # Android: wm size → "Physical size: 1080x2400"
        r = subprocess.run(["wm", "size"], capture_output=True, text=True, timeout=3)
        for token in r.stdout.split():
            if "x" in token and token.replace("x", "").isdigit() is False:
                parts = token.split("x")
                if len(parts) == 2 and all(p.isdigit() for p in parts):
                    _screen_size = (int(parts[0]), int(parts[1]))
                    return _screen_size
    except Exception:
        pass
    # Фолбек через PNG-заголовок screencap
    try:
        r = subprocess.run(["screencap", "-p"], capture_output=True, timeout=5)
        if r.returncode == 0 and len(r.stdout) > 24:
            w = struct.unpack(">I", r.stdout[16:20])[0]
            h = struct.unpack(">I", r.stdout[20:24])[0]
            _screen_size = (w, h)
            return _screen_size
    except Exception:
        pass
    return (1080, 1920)  # safe default


def _capture_frame_png() -> bytes | None:
    """Снимает скриншот, возвращает PNG-байты."""
    try:
        r = subprocess.run(["screencap", "-p"], capture_output=True, timeout=4)
        return r.stdout if r.returncode == 0 and r.stdout else None
    except Exception:
        return None


def _capture_frame_jpeg(quality: int = 60) -> bytes | None:
    """Снимает скриншот в JPEG (нужен Pillow). Возвращает None если нет Pillow."""
    try:
        from PIL import Image
        import io
        r = subprocess.run(["screencap", "-p"], capture_output=True, timeout=4)
        if r.returncode != 0 or not r.stdout:
            return None
        img = Image.open(io.BytesIO(r.stdout))
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="JPEG", quality=quality, optimize=False)
        return buf.getvalue()
    except Exception:
        return None


# Проверяем Pillow один раз при запуске
try:
    from PIL import Image as _PIL_Image
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


# ── TLS: генерация самоподписанного сертификата ────────
def _gen_cert() -> bool:
    """Генерирует cert.pem + key.pem через openssl. Возвращает True при успехе."""
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)
    cmd = [
        "openssl", "req", "-x509",
        "-newkey", "rsa:2048",
        "-keyout", str(KEY_FILE),
        "-out",    str(CERT_FILE),
        "-days",   "3650",
        "-nodes",
        "-subj",   "/CN=localhost",
        "-addext", "subjectAltName=IP:127.0.0.1,DNS:localhost",
    ]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=30)
        return r.returncode == 0
    except FileNotFoundError:
        return False   # openssl не установлен
    except Exception:
        return False


def _ensure_cert() -> bool:
    """Проверяет/создаёт сертификат. Возвращает True если HTTPS доступен."""
    if CERT_FILE.exists() and KEY_FILE.exists():
        return True
    print("  ⚙  Генерирую TLS-сертификат (первый запуск)...")
    ok = _gen_cert()
    if ok:
        print(f"  ✓  Сертификат: {CERT_FILE}")
    else:
        # Дать подсказку для Termux
        if IS_TERMUX:
            print("  ✗  openssl не найден. Установи:")
            print("       pkg install openssl-tool")
        else:
            print("  ✗  openssl не найден — HTTPS недоступен.")
        print("     HTTP-агент (порт 7070) продолжает работу.")
    return ok


def _make_ssl_ctx() -> ssl.SSLContext | None:
    try:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(CERT_FILE), keyfile=str(KEY_FILE))
        return ctx
    except Exception as e:
        print(f"  ✗  SSL ошибка: {e}")
        return None


# ── HTTP handler ───────────────────────────────────────
class AgentHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"  [{self.address_string()}] {format % args}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        global CWD

        if self.path == "/":
            self.send_json({
                "name":     "Tovsa RC Agent",
                "version":  "1.1",
                "platform": sys.platform,
                "cwd":      CWD,
                "python":   sys.version.split()[0],
                "termux":   IS_TERMUX,
                "storage":  str(Path.home() / "storage") if IS_TERMUX else None,
                "tls":      self.server.ssl_ctx is not None,
            })
            return

        if self.path == "/cwd":
            self.send_json({"cwd": CWD})
            return

        if self.path == "/ls":
            try:
                entries = []
                p = Path(CWD)
                # Для Termux показываем ярлыки storage при старте
                if IS_TERMUX and str(p) == str(Path("/data/data/com.termux/files/home")):
                    storage_root = p / "storage"
                    if storage_root.exists():
                        for sub in sorted(storage_root.iterdir()):
                            if sub.is_symlink() or sub.is_dir():
                                entries.append({
                                    "name":  sub.name,
                                    "type":  "dir",
                                    "size":  None,
                                    "_path": str(sub.resolve()),
                                })
                for entry in sorted(p.iterdir()):
                    if entry.name == "storage" and IS_TERMUX:
                        continue
                    try:
                        stat = entry.stat()
                        entries.append({
                            "name": entry.name,
                            "type": "dir" if entry.is_dir() else "file",
                            "size": stat.st_size if entry.is_file() else None,
                        })
                    except PermissionError:
                        pass
                self.send_json({"cwd": CWD, "entries": entries})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if self.path == "/screeninfo":
            w, h = _get_screen_size()
            self.send_json({"width": w, "height": h, "pillow": HAS_PILLOW})
            return

        if self.path.startswith("/stream"):
            # Параметр ?q=60 — качество JPEG (если есть Pillow)
            quality = 60
            if "q=" in self.path:
                try: quality = int(self.path.split("q=")[1].split("&")[0])
                except Exception: pass
            quality = max(10, min(95, quality))

            self.send_response(200)
            self.send_header("Content-Type",  "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()

            use_jpeg = HAS_PILLOW
            try:
                while True:
                    t0 = time.monotonic()
                    if use_jpeg:
                        frame = _capture_frame_jpeg(quality)
                        mime  = b"image/jpeg"
                    else:
                        frame = _capture_frame_png()
                        mime  = b"image/png"

                    if frame is None:
                        time.sleep(0.5)
                        continue

                    header = (
                        b"--frame\r\n"
                        b"Content-Type: " + mime + b"\r\n"
                        b"Content-Length: " + str(len(frame)).encode() + b"\r\n\r\n"
                    )
                    self.wfile.write(header + frame + b"\r\n")
                    self.wfile.flush()

                    # Ограничиваем до 15 fps max
                    elapsed = time.monotonic() - t0
                    sleep   = max(0.0, 0.066 - elapsed)
                    if sleep:
                        time.sleep(sleep)

            except (BrokenPipeError, ConnectionResetError, OSError):
                pass  # клиент закрыл соединение
            return

        self.send_json({"error": "Not found"}, 404)

    def do_POST(self):
        global CWD

        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        try:
            data = json.loads(body)
        except Exception:
            self.send_json({"error": "Invalid JSON"}, 400)
            return

        if self.path == "/run":
            cmd     = data.get("cmd", "").strip()
            cwd     = data.get("cwd", CWD)
            timeout = min(int(data.get("timeout", 30)), 120)
            if not cmd:
                self.send_json({"error": "Empty command"}, 400)
                return
            print(f"  RUN: {cmd!r}  (cwd={cwd})")
            try:
                result = subprocess.run(
                    cmd, shell=True, cwd=cwd,
                    capture_output=True, text=True,
                    timeout=timeout, encoding="utf-8", errors="replace",
                )
                self.send_json({"stdout": result.stdout, "stderr": result.stderr,
                                "code": result.returncode, "cmd": cmd})
            except subprocess.TimeoutExpired:
                self.send_json({"error": f"Timeout ({timeout}s)", "code": -1})
            except Exception as e:
                self.send_json({"error": str(e), "code": -1})
            return

        if self.path == "/cd":
            path = data.get("path", "").strip()
            if not path:
                self.send_json({"error": "Empty path"}, 400)
                return
            new_path = (Path(CWD) / path if not os.path.isabs(path) else Path(path)).resolve()
            if not new_path.exists():
                self.send_json({"error": f"Путь не существует: {new_path}"}, 404)
                return
            if not new_path.is_dir():
                self.send_json({"error": f"Не директория: {new_path}"}, 400)
                return
            CWD = str(new_path)
            print(f"  CD: {CWD}")
            self.send_json({"cwd": CWD})
            return

        if self.path == "/open":
            path = data.get("path", "").strip()
            if not path:
                self.send_json({"error": "Empty path"}, 400)
                return
            full = (Path(CWD) / path if not os.path.isabs(path) else Path(path))
            if not full.exists():
                self.send_json({"error": f"Файл не найден: {full}"}, 404)
                return
            try:
                if sys.platform == "darwin":
                    subprocess.Popen(["open", str(full)])
                elif sys.platform == "win32":
                    os.startfile(str(full))
                else:
                    subprocess.Popen(["xdg-open", str(full)])
                print(f"  OPEN: {full}")
                self.send_json({"ok": True, "path": str(full)})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if self.path == "/write":
            path    = data.get("path", "").strip()
            content = data.get("content", "")
            if not path:
                self.send_json({"error": "Empty path"}, 400)
                return
            full = (Path(CWD) / path if not os.path.isabs(path) else Path(path))
            try:
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(content, encoding="utf-8")
                print(f"  WRITE: {full} ({len(content)} chars)")
                self.send_json({"ok": True, "path": str(full)})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if self.path == "/read":
            path = data.get("path", "").strip()
            if not path:
                self.send_json({"error": "Empty path"}, 400)
                return
            full = (Path(CWD) / path if not os.path.isabs(path) else Path(path))
            try:
                if not full.exists():
                    self.send_json({"error": f"Файл не найден: {full}"}, 404)
                    return
                size = full.stat().st_size
                if size > 1_000_000:
                    self.send_json({"error": f"Файл слишком большой: {size} байт"}, 400)
                    return
                content = full.read_text(encoding="utf-8", errors="replace")
                self.send_json({"content": content, "path": str(full), "size": size})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        if self.path == "/input":
            kind = data.get("type", "")
            try:
                w, h = _get_screen_size()
                if kind == "tap":
                    x = int(float(data["x"]) * w)
                    y = int(float(data["y"]) * h)
                    subprocess.run(["input", "tap", str(x), str(y)], timeout=3)
                    self.send_json({"ok": True})

                elif kind == "swipe":
                    x1 = int(float(data["x1"]) * w)
                    y1 = int(float(data["y1"]) * h)
                    x2 = int(float(data["x2"]) * w)
                    y2 = int(float(data["y2"]) * h)
                    ms = int(data.get("ms", 200))
                    subprocess.run(
                        ["input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms)],
                        timeout=3,
                    )
                    self.send_json({"ok": True})

                elif kind == "text":
                    # Экранируем для shell
                    text = str(data.get("text", "")).replace("\\", "\\\\").replace(" ", "%s")
                    subprocess.run(["input", "text", text], timeout=3)
                    self.send_json({"ok": True})

                elif kind == "key":
                    # keycode: HOME=3, BACK=4, MENU=82, POWER=26, VOLUME_UP=24, VOLUME_DOWN=25
                    keycode = str(data.get("keycode", ""))
                    if keycode:
                        subprocess.run(["input", "keyevent", keycode], timeout=3)
                    self.send_json({"ok": True})

                else:
                    self.send_json({"error": f"Unknown input type: {kind}"}, 400)

            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        self.send_json({"error": "Unknown endpoint"}, 404)


# ── Сервер с опциональным SSL (многопоточный) ──────────
class TLSHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True  # потоки умирают вместе с процессом

    def __init__(self, addr, handler, ssl_ctx=None):
        super().__init__(addr, handler)
        self.ssl_ctx = ssl_ctx

    def get_request(self):
        sock, addr = super().get_request()
        if self.ssl_ctx:
            sock = self.ssl_ctx.wrap_socket(sock, server_side=True)
        return sock, addr


def main():
    _ensure_cert()
    ssl_ctx = _make_ssl_ctx()

    if not ssl_ctx:
        hint = "pkg install openssl-tool" if IS_TERMUX else "установи openssl"
        print(f"\n  ✗  HTTPS недоступен. {hint} и перезапусти агент.\n")
        sys.exit(1)

    try:
        server = TLSHTTPServer((HOST, PORT), AgentHandler, ssl_ctx=ssl_ctx)
    except OSError as e:
        print(f"\n  ✗  Порт {PORT} занят: {e}\n")
        sys.exit(1)

    print(f"""
╔════════════════════════════════════════╗
║         Tovsa RC Agent v1.2            ║
╚════════════════════════════════════════╝

  Адрес:    https://{HOST}:{PORT}
  Папка:    {CWD}
  Платформа:{sys.platform}{'  (Termux)' if IS_TERMUX else ''}
  JPEG:     {'✓ Pillow' if HAS_PILLOW else '✗ PNG (pip install pillow для ускорения)'}

  Эндпоинты экрана:
    GET  /screeninfo  — разрешение экрана
    GET  /stream      — MJPEG стрим (?q=60 — качество)
    POST /input       — касания, свайпы, текст, кнопки

  ╔══════════════════════════════════════════════════════╗
  ║  Первый раз:                                         ║
  ║  1. Открой https://localhost:{PORT} в Chrome        ║
  ║  2. «Дополнительно» → «Всё равно перейти»           ║
  ║  3. Вернись в Tovsa RC → Агент → Подключить         ║
  ╚══════════════════════════════════════════════════════╝

  Ctrl+C для остановки
""")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Агент остановлен.")
        server.server_close()


if __name__ == "__main__":
    main()
