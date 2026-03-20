#!/usr/bin/env python3
"""
Tovsa RC Agent — Bootstrap
Использование (одна команда):

  Linux/macOS:
    python3 -c "$(curl -fsSL https://tovsa7.github.io/tovsa-rc/bootstrap.py)"

  Windows PowerShell:
    python -c (Invoke-WebRequest https://tovsa7.github.io/tovsa-rc/bootstrap.py).Content

Что делает:
  1. Создаёт папку ~/.tovsa/
  2. Скачивает agent.py с GitHub
  3. Запускает агент на localhost:7070
"""

import os
import sys
import urllib.request
from pathlib import Path

REPO     = "https://tovsa7.github.io/tovsa-rc"
AGENT_URL = f"{REPO}/agent.py"
INSTALL_DIR = Path.home() / ".tovsa"
AGENT_PATH  = INSTALL_DIR / "agent.py"

def main():
    print()
    print("  Tovsa RC Agent — Bootstrap")
    print("  " + "─" * 32)

    # Создать папку
    INSTALL_DIR.mkdir(parents=True, exist_ok=True)

    # Скачать agent.py
    print(f"  ↓ Скачиваю agent.py с GitHub...")
    try:
        req = urllib.request.Request(
            AGENT_URL,
            headers={"User-Agent": "TovsaRC-Bootstrap/1.0"}
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
        AGENT_PATH.write_bytes(content)
        print(f"  ✓ Сохранён: {AGENT_PATH}")
    except Exception as e:
        print(f"  ✗ Ошибка загрузки: {e}")
        if AGENT_PATH.exists():
            print(f"  → Использую существующий: {AGENT_PATH}")
        else:
            print("  Нет сети и нет локальной копии. Скачай agent.py вручную.")
            sys.exit(1)

    # Скачиваем index.html для раздачи с localhost
    HTML_URL  = f"{REPO}/index.html"
    HTML_PATH = INSTALL_DIR / "index.html"
    print(f"  ↓ Скачиваю index.html...")
    try:
        req = urllib.request.Request(HTML_URL, headers={"User-Agent": "TovsaRC-Bootstrap/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            content = resp.read()
        HTML_PATH.write_bytes(content)
        print(f"  ✓ Сохранён: {HTML_PATH}")
    except Exception as e:
        print(f"  ✗ Не удалось скачать index.html: {e} (агент продолжит работу)")

    print(f"  Python: {sys.version.split()[0]}")
    print()
    print(f"  Запускаю агент на http://localhost:7070 ...")
    print(f"  Ctrl+C для остановки")
    print()

    # Запустить agent.py в том же процессе
    exec(AGENT_PATH.read_text(encoding="utf-8"), {"__name__": "__main__"})

main()
