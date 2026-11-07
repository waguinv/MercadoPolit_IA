#!/usr/bin/env bash
# Liga a atualização automática: a cada 10 minutos o servidor confere o GitHub e roda atualizar_automatico.sh.
# Só cria os dois arquivos mercadopilot-atualizar no systemd; não mexe nos outros sistemas.
# Uso: sudo ./instalar_atualizacao_automatica.sh            (liga)
#      sudo ./instalar_atualizacao_automatica.sh --remover  (desliga e apaga os dois arquivos)
set -euo pipefail
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
unidades="${MP_SYSTEMD_DIR:-/etc/systemd/system}"
nome="mercadopilot-atualizar"

if [ "${1:-}" = "--remover" ]; then
  systemctl disable --now "$nome.timer" 2> /dev/null || true
  rm -f "$unidades/$nome.timer" "$unidades/$nome.service"
  systemctl daemon-reload
  echo "Atualização automática desligada."
  exit 0
fi

for programa in git curl python3 systemctl; do
  command -v "$programa" > /dev/null || { echo "Falta o programa $programa. Nada foi alterado."; exit 1; }
done
[ -f "$dir/.env.producao" ] || { echo "Falta $dir/.env.producao: implante uma vez à mão antes."; exit 1; }

cat > "$unidades/$nome.service" <<EOF
[Unit]
Description=MercadoPilot: atualiza a partir do GitHub quando o CI aprova
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=oneshot
ExecStart=$dir/atualizar_automatico.sh
# Prioridade baixa: o servidor é compartilhado com outros sistemas.
Nice=10
IOSchedulingClass=idle
EOF

cat > "$unidades/$nome.timer" <<EOF
[Unit]
Description=MercadoPilot: confere atualizações a cada 10 minutos

[Timer]
OnBootSec=5min
OnUnitActiveSec=10min

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now "$nome.timer"
echo "Atualização automática ligada. Acompanhe com: journalctl -u $nome -n 50"
