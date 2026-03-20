#!/bin/sh
# Tovsa RC Agent — установка и запуск
# Использование: curl -fsSL https://tovsa7.github.io/tovsa-rc/install.sh | sh

set -e

REPO="https://tovsa7.github.io/tovsa-rc"
INSTALL_DIR="$HOME/.tovsa"
AGENT_FILE="$INSTALL_DIR/agent.py"

echo ""
echo "  Tovsa RC Agent — установка"
echo "  ────────────────────────────"

# Создать папку
mkdir -p "$INSTALL_DIR"

# Скачать agent.py
echo "  ↓ Скачиваю agent.py..."
if command -v curl >/dev/null 2>&1; then
    curl -fsSL "$REPO/agent.py" -o "$AGENT_FILE"
elif command -v wget >/dev/null 2>&1; then
    wget -q "$REPO/agent.py" -O "$AGENT_FILE"
else
    echo "  ✗ Нужен curl или wget"
    exit 1
fi

echo "  ✓ Сохранён: $AGENT_FILE"

# Найти Python
PYTHON=""
for cmd in python3 python; do
    if command -v $cmd >/dev/null 2>&1; then
        PYTHON=$cmd
        break
    fi
done

if [ -z "$PYTHON" ]; then
    echo "  ✗ Python не найден. Установи Python 3 и запусти:"
    echo "    python3 $AGENT_FILE"
    exit 1
fi

PY_VER=$($PYTHON --version 2>&1)
echo "  ✓ Python: $PY_VER"
echo ""
echo "  Запускаю агент на http://localhost:7070 ..."
echo "  Ctrl+C для остановки"
echo ""

exec $PYTHON "$AGENT_FILE"
