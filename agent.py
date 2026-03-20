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
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

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

        self.send_json({"error": "Unknown endpoint"}, 404)


# ── Сервер с опциональным SSL ──────────────────────────
class TLSHTTPServer(HTTPServer):
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
║         Tovsa RC Agent v1.1            ║
╚════════════════════════════════════════╝

  Адрес:    https://{HOST}:{PORT}
  Папка:    {CWD}
  Платформа:{sys.platform}{'  (Termux)' if IS_TERMUX else ''}

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
