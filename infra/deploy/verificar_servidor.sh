#!/usr/bin/env bash
# SOMENTE LEITURA: não instala nem altera nada, e não mostra senhas nem conteúdo de .env.
# Rode no servidor antes de implantar: sudo bash verificar_servidor.sh
set -u
PORTA="${BACKEND_PORT:-8011}"
DOMINIO="gestor.sysordemsistemas.com.br"
REDE="172.31.250."

secao() { printf '\n=== %s\n' "$1"; }
versao() { command -v "$1" >/dev/null 2>&1 && "$@" 2>&1 | head -1 || echo "$1: NÃO INSTALADO"; }

secao "Sistema"
grep PRETTY_NAME /etc/os-release
free -h | sed -n '1,2p'
df -h / | tail -1
echo "CPUs: $(nproc)"

secao "Programas"
versao docker --version
docker compose version 2>&1 | head -1
versao nginx -v
versao certbot --version
versao git --version

secao "Porta $PORTA (precisa estar livre)"
ss -ltn "sport = :$PORTA" | tail -n +2 | grep . || echo "livre"

secao "Portas em uso (sem detalhes de processo)"
ss -ltn | awk 'NR>1 {print $4}' | sort -u

secao "Sites no nginx"
ls -1 /etc/nginx/sites-enabled/ 2>/dev/null || echo "sem sites-enabled"
nginx -t 2>&1 | tail -1
echo "server_name configurados:"
grep -RhoE '^\s*server_name\s+[^;]+' /etc/nginx/sites-enabled/ 2>/dev/null | sed 's/^\s*//' | sort -u
grep -rl "$DOMINIO" /etc/nginx/ 2>/dev/null | sed 's/^/ATENÇÃO, já cita o domínio: /'

secao "Docker: contêineres e redes"
docker ps --format '{{.Names}}\t{{.Image}}\t{{.Status}}' 2>&1
docker network ls --format '{{.Name}}' 2>/dev/null | while read -r rede; do
  faixa="$(docker network inspect "$rede" --format '{{range .IPAM.Config}}{{.Subnet}} {{end}}' 2>/dev/null)"
  echo "$rede: $faixa"
  case "$faixa" in *"$REDE"*) [ "$rede" = "mercadopilot_default" ] || echo "ATENÇÃO: conflita com a rede do MercadoPilot" ;; esac
done

secao "DNS e certificados"
echo "$DOMINIO aponta para: $(getent hosts "$DOMINIO" | awk '{print $1}' | sort -u | tr '\n' ' ')"
echo "este servidor: $(hostname -I)"
ls -1 /etc/letsencrypt/live/ 2>/dev/null | grep -v README || echo "sem certificados Let's Encrypt"
systemctl is-active certbot.timer 2>/dev/null | sed 's/^/renovação automática (certbot.timer): /'

secao "Firewall"
command -v ufw >/dev/null 2>&1 && ufw status 2>&1 | head -12 || echo "ufw não instalado"

printf '\nFim. Nada foi alterado.\n'
