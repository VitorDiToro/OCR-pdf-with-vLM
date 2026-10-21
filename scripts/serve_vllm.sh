#!/usr/bin/env bash
# Sobe o modelo de OCR num servidor vLLM (API compatível com OpenAI em :8000).
# O vLLM roda num ambiente isolado criado pelo uvx, separado do ambiente do ocrpdf,
# porque traz o PyTorch/CUDA (alguns GB) e não precisa ser importado pela aplicação.
#
# Versão do vLLM recomendada pelo dots.mocr: 0.17.1 (sobrescreva com VLLM_VERSION=...).
# Uso: scripts/serve_vllm.sh [modelo] [porta]
#   modelo padrão: rednote-hilab/dots.mocr (~3B; cabe em GPUs de 8 GB)
#   alternativa : Qwen/Qwen3-VL-8B-Instruct-FP8 (precisa de ~12 GB; use --preset qwen3vl)
# Variáveis: GPU_MEM (fração da VRAM), MAX_LEN, MAX_SEQS, VLLM_VERSION,
#   HOST (padrão 127.0.0.1: o servidor não fica exposto na rede),
#   SLEEP_MODE=1 (modo sleep: o servidor pode dormir/acordar via /sleep e /wake_up,
#   para dois modelos dividirem a GPU; usado por scripts/serve_pair.sh). Com ele:
#   --enable-sleep-mode, VLLM_SERVER_DEV_MODE=1 (expõe os endpoints) e
#   --mm-processor-cache-gb 0 (contorno de defeito do vLLM 0.17.1: após /sleep, uma imagem
#   repetida falha com "Expected a cached item for mm_hash"). Medido numa RTX 3060: partida
#   do dots.mocr ~2 min; dormir ~1 s, acordar ~1,3 s; dormindo, retém ~232 MiB de VRAM.
#   MAX_IMAGE_PX (só modelos Qwen3-VL, como o 2º modelo): limita a imagem a tantos pixels e desliga
#   vídeo; sem isso o vLLM reserva memória para a imagem máxima (16,7 MP) e um 8B não cabe em 12 GB.
set -euo pipefail

MODEL="${1:-rednote-hilab/dots.mocr}"
PORT="${2:-8000}"
VLLM_VERSION="${VLLM_VERSION:-0.17.1}"

# O vLLM exige que a fração GPU_MEM da VRAM esteja livre na partida. Num desktop, Xorg,
# gnome-shell e navegador já ocupam ~1 GB, e um 0,90 fixo falha. Sem GPU_MEM, usa a memória
# livre agora menos 512 MiB de folga (no máximo 0,90).
if [[ -z "${GPU_MEM:-}" ]]; then
    read -r FREE TOTAL < <(nvidia-smi --query-gpu=memory.free,memory.total \
        --format=csv,noheader,nounits | head -1 | tr -d ',')
    GPU_MEM=$(LC_ALL=C awk -v f="$FREE" -v t="$TOTAL" \
        'BEGIN { u = (f - 512) / t; if (u > 0.90) u = 0.90; printf "%.2f", int(u * 100) / 100 }')
    echo "GPU_MEM=${GPU_MEM} (livre: ${FREE} de ${TOTAL} MiB)" >&2
fi

EXTRA=()
if [[ "${SLEEP_MODE:-0}" == "1" ]]; then
    export VLLM_SERVER_DEV_MODE=1
    EXTRA+=(--enable-sleep-mode --mm-processor-cache-gb 0)
fi
if [[ -n "${MAX_IMAGE_PX:-}" ]]; then
    EXTRA+=(--limit-mm-per-prompt '{"image":1,"video":0}'
            --mm-processor-kwargs "{\"size\":{\"longest_edge\":${MAX_IMAGE_PX},\"shortest_edge\":65536}}")
fi

# Contexto de 16k tokens: uma página em ~200 dpi gera ~4-5k tokens de imagem + a resposta.
# Em GPUs de 8 GB, se faltar memória, reduza para 12288 ou baixe o --dpi do ocrpdf.
exec uvx --python 3.12 --from "vllm==${VLLM_VERSION}" vllm serve "${MODEL}" \
    --host "${HOST:-127.0.0.1}" \
    --port "${PORT}" \
    --served-model-name model \
    --trust-remote-code \
    --chat-template-content-format string \
    --gpu-memory-utilization "${GPU_MEM}" \
    --max-model-len "${MAX_LEN:-16384}" \
    --max-num-seqs "${MAX_SEQS:-4}" \
    "${EXTRA[@]}"
