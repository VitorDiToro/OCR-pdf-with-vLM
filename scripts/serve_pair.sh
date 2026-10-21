#!/usr/bin/env bash
# Só para o itálico com 2º modelo (ocrpdf --italic-rule r1|r2); a regra padrão (slant) precisa
# apenas de scripts/serve_vllm.sh.
# Sobe o par de servidores vLLM do modo sleep: dots.mocr (OCR) em :8000 e o 2º modelo (marcação de
# itálico) em :8001. Os dois ficam no ar na mesma GPU; só um fica acordado por vez e a aplicação os
# alterna com /sleep e /wake_up (ver src/ocrpdf/servers.py).
# Partida do dots.mocr ~2 min numa RTX 3060; depois de ele dormir, o 2º modelo sobe com a memória
# que sobrou (serve_vllm.sh calcula GPU_MEM pela VRAM livre).
# Cuidado num desktop: com o par no ar sobra ~0,5 GiB de VRAM para a interface gráfica; numa
# execução assim o gnome-shell caiu e levou a sessão junto. Feche programas que usam a GPU.
#
# Uso: scripts/serve_pair.sh [modelo_italico]
#   padrão: cyankiwi/Qwen3-VL-8B-Instruct-AWQ-4bit (o mesmo de ITALIC_HF_MODEL em src/ocrpdf/servers.py)
# Variáveis: LOG_DIR (padrão /tmp; logs ocrpdf-ocr.log e ocrpdf-italic.log) e as de serve_vllm.sh.
# Ctrl+C (ou falha na partida) derruba os dois servidores.
set -euo pipefail

ITALIC_MODEL="${1:-cyankiwi/Qwen3-VL-8B-Instruct-AWQ-4bit}"
LOG_DIR="${LOG_DIR:-/tmp}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SLEEP_MODE=1

PIDS=()
cleanup() {
    trap - EXIT INT TERM
    for pid in "${PIDS[@]}"; do
        # cada servidor é líder do próprio grupo (setsid): o sinal vai ao grupo inteiro
        kill -INT -- "-${pid}" 2>/dev/null || true
    done
    # vLLM preso no encerramento (EngineCore em NCCL/CUDA) não pode prender o terminal nem a GPU:
    # o vigia manda SIGKILL ao grupo depois de STOP_TIMEOUT s (padrão 60)
    ( sleep "${STOP_TIMEOUT:-60}"
      for pid in "${PIDS[@]}"; do kill -KILL -- "-${pid}" 2>/dev/null || true; done ) &
    local guard=$!
    for pid in "${PIDS[@]}"; do wait "${pid}" 2>/dev/null || true; done
    kill "${guard}" 2>/dev/null || true
    # processos do grupo que sobreviveram ao líder (o vLLM cria processos de trabalho)
    for pid in "${PIDS[@]}"; do kill -KILL -- "-${pid}" 2>/dev/null || true; done
}
trap cleanup EXIT
trap 'echo "Encerrando os servidores..." >&2; exit 130' INT TERM

start() {  # nome modelo porta
    setsid "${HERE}/serve_vllm.sh" "$2" "$3" >"${LOG_DIR}/ocrpdf-$1.log" 2>&1 &
    PIDS+=("$!")
    echo "[$1] $2 na porta $3 (log: ${LOG_DIR}/ocrpdf-$1.log)" >&2
}

wait_ready() {  # nome porta pid
    until curl -sf "http://127.0.0.1:$2/v1/models" >/dev/null 2>&1; do
        if ! kill -0 "$3" 2>/dev/null; then
            echo "[$1] o servidor terminou antes de ficar pronto; fim do log:" >&2
            tail -n 20 "${LOG_DIR}/ocrpdf-$1.log" >&2
            exit 1
        fi
        sleep 2
    done
    echo "[$1] pronto" >&2
}

start ocr rednote-hilab/dots.mocr 8000
wait_ready ocr 8000 "${PIDS[0]}"
# Dorme o dots para liberar a VRAM antes de o 2º modelo medir a memória livre.
curl -sf -X POST "http://127.0.0.1:8000/sleep?level=1" >/dev/null
echo "[ocr] dormindo" >&2

# mesmos limites de ITALIC_SERVER_ENV (src/ocrpdf/servers.py): recortes pequenos, contexto curto
MAX_IMAGE_PX=1605632 MAX_LEN=8192 MAX_SEQS=2 start italic "${ITALIC_MODEL}" 8001
wait_ready italic 8001 "${PIDS[1]}"
echo "par no ar: ocr :8000 (dormindo), italic :8001 (acordado). Ctrl+C para encerrar." >&2
# A aplicação alterna os dois; aqui só esperamos Ctrl+C ou a queda de um servidor.
wait -n "${PIDS[@]}" || true
echo "um dos servidores terminou; encerrando o outro." >&2
