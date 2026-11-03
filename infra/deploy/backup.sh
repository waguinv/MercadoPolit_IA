#!/usr/bin/env bash
# Cópia do banco, com conferência. Guarda BACKUP_KEEP dias. Copie também para FORA do servidor.
# Agendamento sugerido no cron: 0 3 * * * /opt/mercadopilot/infra/deploy/backup.sh
set -euo pipefail
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/mercadopilot}"
BACKUP_KEEP="${BACKUP_KEEP:-14}"

umask 077
mkdir -p "$BACKUP_DIR"
arquivo="$BACKUP_DIR/mercadopilot-$(date +%Y%m%d-%H%M%S).sql.gz"
"$dir/compose.sh" exec -T db pg_dump -U mercadopilot -d mercadopilot --clean --if-exists | gzip > "$arquivo.parcial"
gzip -t "$arquivo.parcial"
# Dump cortado no meio não tem esta linha final.
gzip -dc "$arquivo.parcial" | tail -5 | grep -q 'PostgreSQL database dump complete' || {
  rm -f "$arquivo.parcial"; echo "Backup incompleto: descartado."; exit 1; }
mv "$arquivo.parcial" "$arquivo"
find "$BACKUP_DIR" -name 'mercadopilot-*.sql.gz' -mtime +"$BACKUP_KEEP" -delete
echo "Backup: $arquivo"
