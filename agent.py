#!/usr/bin/env python3
"""
Tovsa RC Agent v1.2
Слушает на http://localhost:7070
Chrome разрешает fetch() к localhost из HTTPS-страниц (W3C Secure Contexts).
Требования: Python 3.7+, никаких зависимостей. Для стрима: pip install pillow
"""

import json
import os
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from socketserver import ThreadingMixIn

PORT = 7070
HOST = "localhost"

INSTALL_DIR = Path.home() / ".tovsa"


def _default_cwd():
    termux_home = Path("/data/data/com.termux/files/home")
    if termux_home.exists():
        shared = termux_home / "storage" / "shared"
        if shared.exists():
            return str(shared)
        return str(termux_home)
    return str(Path.home())

CWD       = _default_cwd()
IS_TERMUX = Path("/data/data/com.termux/files/home").exists()

_screen_size = None

def _get_screen_size():
    global _screen_size
    if _screen_size:
        return _screen_size
    try:
        r = subprocess.run(["wm", "size"], capture_output=True, text=True, timeout=3)
        for token in r.stdout.split():
            if "x" in token:
                parts = token.split("x")
                if len(parts) == 2 and all(p.isdigit() for p in parts):
                    _screen_size = (int(parts[0]), int(parts[1]))
                    return _screen_size
    except Exception:
        pass
    _screen_size = (1080, 1920)
    return _screen_size


def _capture_png():
    try:
        r = subprocess.run(["screencap", "-p"], capture_output=True, timeout=4)
        return r.stdout if r.returncode == 0 and r.stdout else None
    except Exception:
        return None


def _capture_jpeg(quality=65):
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


try:
    from PIL import Image as _pil
    HAS_PILLOW = True
except ImportError:
    HAS_PILLOW = False


class AgentHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        print(f"  [{self.address_string()}] {format % args}")

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin",  "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type",   "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        global CWD

        # Отдаём index.html — полноценный Tovsa RC без mixed content
        STATIC = {
            "/app":           ("index.html",   "text/html; charset=utf-8"),
            "/app/":          ("index.html",   "text/html; charset=utf-8"),
            "/index.html":    ("index.html",   "text/html; charset=utf-8"),
            "/manifest.json": ("manifest.json","application/manifest+json"),
            "/sw.js":         ("sw.js",        "application/javascript"),
        }
        if self.path in STATIC:
            filename, mime = STATIC[self.path]
            file_path = INSTALL_DIR / filename
            if not file_path.exists():
                self.send_json({"error": f"{filename} не найден — перезапусти bootstrap"}, 404)
                return
            body = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type",   mime)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if self.path == "/":
            self.send_json({
                "name": "Tovsa RC Agent", "version": "1.2",
                "platform": sys.platform, "cwd": CWD,
                "python": sys.version.split()[0],
                "termux": IS_TERMUX,
                "storage": str(Path.home() / "storage") if IS_TERMUX else None,
                "pillow": HAS_PILLOW,
                "app": f"http://localhost:{PORT}/app",
            })
            return

        if self.path == "/cwd":
            self.send_json({"cwd": CWD})
            return

        if self.path == "/ls":
            try:
                entries = []
                p = Path(CWD)
                if IS_TERMUX and str(p) == str(Path("/data/data/com.termux/files/home")):
                    storage_root = p / "storage"
                    if storage_root.exists():
                        for sub in sorted(storage_root.iterdir()):
                            if sub.is_symlink() or sub.is_dir():
                                entries.append({"name": sub.name, "type": "dir", "size": None,
                                                "_path": str(sub.resolve())})
                for entry in sorted(p.iterdir()):
                    if entry.name == "storage" and IS_TERMUX:
                        continue
                    try:
                        stat = entry.stat()
                        entries.append({"name": entry.name,
                                        "type": "dir" if entry.is_dir() else "file",
                                        "size": stat.st_size if entry.is_file() else None})
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
            quality = 65
            if "q=" in self.path:
                try: quality = int(self.path.split("q=")[1].split("&")[0])
                except Exception: pass
            quality = max(10, min(95, quality))

            self.send_response(200)
            self.send_header("Content-Type",  "multipart/x-mixed-replace; boundary=frame")
            self.send_header("Cache-Control", "no-cache, no-store")
            self._cors()
            self.end_headers()

            try:
                while True:
                    t0    = time.monotonic()
                    frame = _capture_jpeg(quality) if HAS_PILLOW else _capture_png()
                    mime  = b"image/jpeg" if HAS_PILLOW else b"image/png"
                    if frame is None:
                        time.sleep(0.5)
                        continue
                    self.wfile.write(
                        b"--frame\r\nContent-Type: " + mime +
                        b"\r\nContent-Length: " + str(len(frame)).encode() +
                        b"\r\n\r\n" + frame + b"\r\n"
                    )
                    self.wfile.flush()
                    elapsed = time.monotonic() - t0
                    sleep = max(0.0, 0.066 - elapsed)
                    if sleep:
                        time.sleep(sleep)
            except (BrokenPipeError, ConnectionResetError, OSError):
                pass
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
                result = subprocess.run(cmd, shell=True, cwd=cwd, capture_output=True,
                                        text=True, timeout=timeout, encoding="utf-8", errors="replace")
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
                    x1 = int(float(data["x1"]) * w);  y1 = int(float(data["y1"]) * h)
                    x2 = int(float(data["x2"]) * w);  y2 = int(float(data["y2"]) * h)
                    ms = int(data.get("ms", 200))
                    subprocess.run(["input", "swipe", str(x1), str(y1), str(x2), str(y2), str(ms)], timeout=3)
                    self.send_json({"ok": True})
                elif kind == "text":
                    text = str(data.get("text", "")).replace("\\", "\\\\").replace(" ", "%s")
                    subprocess.run(["input", "text", text], timeout=3)
                    self.send_json({"ok": True})
                elif kind == "key":
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


class AgentServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def main():
    try:
        server = AgentServer((HOST, PORT), AgentHandler)
    except OSError as e:
        print(f"\n  ✗  Порт {PORT} занят: {e}\n")
        sys.exit(1)

    print(f"""
╔════════════════════════════════════════╗
║         Tovsa RC Agent v1.2            ║
╚════════════════════════════════════════╝

  API:      http://{HOST}:{PORT}
  Приложение: http://{HOST}:{PORT}/app
  Папка:    {CWD}
  Платформа:{sys.platform}{'  (Termux)' if IS_TERMUX else ''}
  JPEG:     {'✓ Pillow' if HAS_PILLOW else '✗ PNG (pip install pillow для ускорения)'}

  Открой в Chrome: http://localhost:{PORT}/app

  Ctrl+C для остановки
""")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  Агент остановлен.")
        server.server_close()


if __name__ == "__main__":
    main()
