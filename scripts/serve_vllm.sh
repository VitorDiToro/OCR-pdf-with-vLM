#!/usr/bin/env bash
# Sobe o modelo de OCR num servidor vLLM (API compatível com OpenAI em :8000).
# O vLLM roda num ambiente isolado criado pelo uvx, separado do ambiente do ocrpdf,
# porque traz o PyTorch/CUDA (alguns GB) e não precisa ser importado pela aplicação.
#
# Versão do vLLM recomendada pelo dots.mocr: 0.17.1 (sobrescreva com VLLM_VERSION=...).
# Uso: scripts/serve_vllm.sh [modelo] [porta]
#   modelo padrão: rednote-hilab/dots.mocr (~3B; cabe em GPUs de 8 GB)
#   alternativa : Qwen/Qwen3-VL-8B-Instruct-FP8 (precisa de ~12 GB; use --preset qwen3vl)
set -euo pipefail

MODEL="${1:-rednote-hilab/dots.mocr}"
PORT="${2:-8000}"
VLLM_VERSION="${VLLM_VERSION:-0.17.1}"

# Contexto de 16k tokens: uma página em ~200 dpi gera ~4-5k tokens de imagem + a resposta.
# Em GPUs de 8 GB, se faltar memória, reduza para 12288 ou baixe o --dpi do ocrpdf.
exec uvx --python 3.12 --from "vllm==${VLLM_VERSION}" vllm serve "${MODEL}" \
    --port "${PORT}" \
    --served-model-name model \
    --trust-remote-code \
    --chat-template-content-format string \
    --gpu-memory-utilization "${GPU_MEM:-0.90}" \
    --max-model-len "${MAX_LEN:-16384}" \
    --max-num-seqs "${MAX_SEQS:-4}"
