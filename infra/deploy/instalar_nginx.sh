#!/usr/bin/env bash
# Instala SÓ o site do MercadoPilot no nginx. Não lê nem altera os outros sites do servidor.
# Se a configuração não passar em "nginx -t", desfaz a própria instalação e não recarrega nada.
# Uso: sudo ./instalar_nginx.sh
set -euo pipefail
raiz="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
DOMINIO="gestor.sysordemsistemas.com.br"
site="/etc/nginx/sites-available/$DOMINIO"
link="/etc/nginx/sites-enabled/$DOMINIO"
snippet="/etc/nginx/snippets/gestor-seguranca.conf"

# O certbot acrescenta o HTTPS neste arquivo. Copiar de novo apagaria o certificado da configuração.
if [ -e "$site" ] && [ "${1:-}" != "--substituir" ]; then
  echo "O site já existe em $site. Nada foi alterado."
  echo "Para substituir (e depois rodar o certbot de novo), use: --substituir"
  exit 1
fi

nginx -t  # Se já houver erro de outro site, não mexe em nada.
mkdir -p /etc/nginx/snippets
install -m 644 "$raiz/infra/nginx/gestor-seguranca.conf" "$snippet"
install -m 644 "$raiz/infra/nginx/gestor.conf" "$site"
ln -sf "$site" "$link"

if ! nginx -t; then
  rm -f "$link" "$site" "$snippet"
  echo "Configuração recusada: instalação desfeita. O nginx não foi recarregado."
  exit 1
fi
systemctl reload nginx
echo "Site instalado em HTTP. Próximo passo: sudo certbot --nginx -d $DOMINIO"
