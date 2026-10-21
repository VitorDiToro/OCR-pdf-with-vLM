#!/usr/bin/env bash
# Substitui scripts/serve_vllm.sh nos testes: $1 = modelo (ignorado), $2 = porta.
exec python3 "$(dirname "$0")/fake_server.py" "$2"
