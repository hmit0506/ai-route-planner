#!/bin/bash
set -e

echo "==> 检查 Python 版本..."
PYTHON=""
for cmd in python3.12 python3 python; do
    if command -v $cmd &>/dev/null; then
        VER=$($cmd -c "import sys; print(sys.version_info >= (3,11))" 2>/dev/null)
        if [ "$VER" = "True" ]; then
            PYTHON=$cmd
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "错误：需要 Python 3.11+，请先安装。"
    exit 1
fi
echo "    使用 $PYTHON ($($PYTHON --version))"

echo "==> 创建虚拟环境 .venv ..."
$PYTHON -m venv .venv

echo "==> 安装依赖..."
.venv/bin/python3 -m pip install -q --upgrade pip setuptools
.venv/bin/python3 -m pip install -q -e .

echo "==> 检查 .env 文件..."
if [ ! -f .env ]; then
    cp .env.example .env
    echo "    已生成 .env，请填入 API Key 后再启动服务。"
else
    echo "    .env 已存在，跳过。"
fi

echo ""
echo "环境准备完成！启动服务（poi.db 将在首次启动时自动生成）："
echo "    PYTHONPATH=. .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload"
