#!/usr/bin/env python3
"""
Tovsa RC Agent
Локальный агент для выполнения команд от Tovsa Remote Client.

Запуск:
    python agent.py

По умолчанию слушает на localhost:7070
Доступен ТОЛЬКО локально — не открывать порт наружу!

Требования: Python 3.7+, никаких зависимостей
"""

import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

PORT = 7070
HOST = "127.0.0.1"  # только localhost!

# Рабочая директория по умолчанию
# На Termux (Android) используем ~/storage/shared если доступно
def _default_cwd():
    # Termux detection
    termux_home = Path("/data/data/com.termux/files/home")
    if termux_home.exists():
        shared = termux_home / "storage" / "shared"
        if shared.exists():
            return str(shared)   # /sdcard — весь накопитель
        return str(termux_home)  # ~/  в Termux
    return str(Path.home())

CWD = _default_cwd()
IS_TERMUX = Path("/data/data/com.termux/files/home").exists()


class AgentHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        # Кастомный лог
        print(f"[{self.address_string()}] {format % args}")

    def send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        global CWD

        if self.path == "/":
            self.send_json({
                "name": "Tovsa RC Agent",
                "version": "1.0",
                "platform": sys.platform,
                "cwd": CWD,
                "python": sys.version.split()[0],
                "termux": IS_TERMUX,
                "storage": str(Path.home() / "storage") if IS_TERMUX else None,
            })
            return

        if self.path == "/cwd":
            self.send_json({"cwd": CWD})
            return

        if self.path == "/ls":
            try:
                entries = []
                for entry in sorted(Path(CWD).iterdir()):
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
        body = self.rfile.read(length)

        try:
            data = json.loads(body)
        except Exception:
            self.send_json({"error": "Invalid JSON"}, 400)
            return

        # ── Выполнить команду ──────────────────────────────
        if self.path == "/run":
            cmd = data.get("cmd", "").strip()
            cwd = data.get("cwd", CWD)
            timeout = min(int(data.get("timeout", 30)), 120)  # макс 2 минуты

            if not cmd:
                self.send_json({"error": "Empty command"}, 400)
                return

            print(f"  RUN: {cmd!r}  (cwd={cwd})")

            try:
                result = subprocess.run(
                    cmd,
                    shell=True,
                    cwd=cwd,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    encoding="utf-8",
                    errors="replace",
                )
                self.send_json({
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "code":   result.returncode,
                    "cmd":    cmd,
                })
            except subprocess.TimeoutExpired:
                self.send_json({"error": f"Timeout ({timeout}s)", "code": -1})
            except Exception as e:
                self.send_json({"error": str(e), "code": -1})
            return

        # ── Сменить директорию ─────────────────────────────
        if self.path == "/cd":
            path = data.get("path", "").strip()
            if not path:
                self.send_json({"error": "Empty path"}, 400)
                return

            new_path = Path(CWD) / path if not os.path.isabs(path) else Path(path)
            new_path = new_path.resolve()

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

        # ── Запустить файл (open) ──────────────────────────
        if self.path == "/open":
            path = data.get("path", "").strip()
            if not path:
                self.send_json({"error": "Empty path"}, 400)
                return

            full = Path(CWD) / path if not os.path.isabs(path) else Path(path)

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

        # ── Записать файл ──────────────────────────────────
        if self.path == "/write":
            path = data.get("path", "").strip()
            content = data.get("content", "")
            if not path:
                self.send_json({"error": "Empty path"}, 400)
                return
            full = Path(CWD) / path if not os.path.isabs(path) else Path(path)
            try:
                full.parent.mkdir(parents=True, exist_ok=True)
                full.write_text(content, encoding="utf-8")
                print(f"  WRITE: {full} ({len(content)} chars)")
                self.send_json({"ok": True, "path": str(full)})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        # ── Прочитать файл ─────────────────────────────────
        if self.path == "/read":
            path = data.get("path", "").strip()
            if not path:
                self.send_json({"error": "Empty path"}, 400)
                return
            full = Path(CWD) / path if not os.path.isabs(path) else Path(path)
            try:
                if not full.exists():
                    self.send_json({"error": f"Файл не найден: {full}"}, 404)
                    return
                size = full.stat().st_size
                if size > 1_000_000:  # 1MB limit
                    self.send_json({"error": f"Файл слишком большой: {size} байт"}, 400)
                    return
                content = full.read_text(encoding="utf-8", errors="replace")
                self.send_json({"content": content, "path": str(full), "size": size})
            except Exception as e:
                self.send_json({"error": str(e)}, 500)
            return

        self.send_json({"error": "Unknown endpoint"}, 404)


def main():
    print(f"""
╔════════════════════════════════════════╗
║         Tovsa RC Agent v1.0            ║
╚════════════════════════════════════════╝

  Адрес:    http://{HOST}:{PORT}
  Рабочая папка: {CWD}
  Платформа: {sys.platform}

  Доступные эндпоинты:
    GET  /          — статус агента
    GET  /ls        — список файлов в CWD
    GET  /cwd       — текущая директория
    POST /run       — выполнить команду
    POST /cd        — сменить директорию
    POST /open      — открыть файл (системно)
    POST /write     — записать файл
    POST /read      — прочитать файл

  В Tovsa RC → Команды → Агент → введи http://localhost:{PORT}

  Ctrl+C для остановки
""")

    server = HTTPServer((HOST, PORT), AgentHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nАгент остановлен.")
        server.server_close()


if __name__ == "__main__":
    main()
