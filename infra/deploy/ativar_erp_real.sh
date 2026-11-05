#!/usr/bin/env bash
# Prepara o .env.producao para ler o ERP real: ERP_ADAPTER=real e uma chave para cifrar os tokens.
# Pode ser repetido: uma chave existente nunca é trocada, porque isso invalidaria os tokens salvos.
# Não mostra a chave, não reinicia nada e não escreve em nenhum ERP. Depois, rode implantar.sh.
# Uso: sudo ./ativar_erp_real.sh
set -euo pipefail
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
env="$dir/.env.producao"

[ -f "$env" ] || { echo "Falta $env (copie de .env.example)."; exit 1; }

# Define a variável no arquivo, trocando a linha existente ou acrescentando uma nova.
definir() {
  if grep -q "^$1=" "$env"; then
    sed -i "s|^$1=.*|$1=$2|" "$env"
  else
    printf '%s=%s\n' "$1" "$2" >> "$env"
  fi
}

atual="$(sed -n 's/^SECRETS_ENCRYPTION_KEY=//p' "$env" | tail -1)"
if [ -z "$atual" ]; then
  # Chave Fernet: 32 bytes aleatórios em base64 próprio para URL.
  definir SECRETS_ENCRYPTION_KEY "$(openssl rand -base64 32 | tr '+/' '-_')"
  echo "Chave de cifragem criada. Guarde uma cópia do .env.producao fora do servidor."
else
  echo "A chave de cifragem já existe e foi mantida."
fi

definir ERP_ADAPTER real
chmod 600 "$env"
echo "ERP_ADAPTER=real definido. Próximo passo: sudo $dir/implantar.sh"
