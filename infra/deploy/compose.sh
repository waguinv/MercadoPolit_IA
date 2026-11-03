#!/usr/bin/env bash
# Atalho para o docker compose de produção. Ex.: sudo ./compose.sh logs --tail 50 backend
set -euo pipefail
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec docker compose --env-file "$dir/.env.producao" -f "$dir/docker-compose.yml" "$@"
