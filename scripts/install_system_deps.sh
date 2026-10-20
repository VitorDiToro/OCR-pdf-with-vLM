#!/usr/bin/env bash
# Dependências de sistema (Ubuntu 22.04+) para o ocrpdf.
#   - Pango/HarfBuzz: exigidos pelo WeasyPrint.
#   - Fontes Liberation: métricas compatíveis com Arial/Times/Courier, as fontes
#     mais comuns nos documentos originais.
#   - uv: gerenciador do ambiente Python do projeto.
set -euo pipefail

sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 \
    fontconfig fonts-liberation2 fonts-dejavu-core fonts-noto-core \
    curl ca-certificates

if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
fi

if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv
else
    echo "AVISO: nvidia-smi não encontrado. Instale o driver NVIDIA (ex.: 'sudo ubuntu-drivers install') para rodar o vLLM." >&2
fi
