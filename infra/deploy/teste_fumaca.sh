#!/usr/bin/env bash
# Confere o sistema implantado, passando pelo nginx em HTTP local (ensaio do CI ou antes do certbot).
# Uso: teste_fumaca.sh <e-mail do admin> ; a senha vem de SMOKE_PASSWORD.
# Atenção: erra a senha de propósito em uma conta inexistente, para testar o limite de login.
set -uo pipefail
EMAIL="$1"
HOST="${SMOKE_HOST:-gestor.sysordemsistemas.com.br}"
BASE="http://127.0.0.1"
API="$BASE/api/v1"
falhas=0

ok() { echo "OK    $1"; }
falha() { echo "FALHA $1"; falhas=$((falhas + 1)); [ "${GITHUB_ACTIONS:-}" = "true" ] && echo "::error title=teste de fumaça::$1"; return 0; }
confere() { if [ "$2" = "$3" ]; then ok "$1"; else falha "$1 (esperado $3, veio $2)"; fi; }
tem() { if printf '%s' "$2" | grep -qi -- "$3"; then ok "$1"; else falha "$1 (não encontrei: $3)"; fi; }
nao_tem() { if printf '%s' "$2" | grep -qi -- "$3"; then falha "$1 (encontrei: $3)"; else ok "$1"; fi; }

req() { curl -s --max-time 30 -H "Host: $HOST" "$@"; }
codigo() { req -o /dev/null -w '%{http_code}' "$@"; }
login_json() { printf '{"email":"%s","password":"%s"}' "$1" "$2"; }
post_login() { codigo -H 'Content-Type: application/json' -d "$(login_json "$1" "$2")" "${@:3}" "$API/auth/login"; }

echo "--- interface"
pagina="$(req "$BASE/")"
tem "página inicial" "$pagina" "SysOrdem MercadoPilot"
tem "link direto cai no app" "$(req "$BASE/sessoes/qualquer")" "SysOrdem MercadoPilot"
cabecalho="$(req -D - -o /dev/null "$BASE/")"
tem "CSP" "$cabecalho" "content-security-policy: default-src 'self'"
tem "nosniff" "$cabecalho" "x-content-type-options: nosniff"
tem "página inicial sem cache" "$cabecalho" "cache-control: no-cache"
tem "sw.js sem cache" "$(req -D - -o /dev/null "$BASE/sw.js")" "cache-control: no-cache"
asset="$(printf '%s' "$pagina" | grep -oE '/assets/[^"]+\.js' | head -1)"
tem "assets com cache longo" "$(req -D - -o /dev/null "$BASE$asset")" "immutable"
tem "manifesto" "$(req "$BASE/manifest.webmanifest")" "MercadoPilot"
nao_tem "documentação da API fechada" "$(req "$BASE/docs")" "swagger"
nao_tem "site não revela versão do nginx" "$cabecalho" "server: nginx/"

echo "--- portas"
portas="$(ss -ltn | awk 'NR>1 {print $4}')"
nao_tem "banco sem porta publicada" "$portas" ":5432"
tem "backend só em 127.0.0.1" "$portas" "127.0.0.1:${BACKEND_PORT:-8011}"

echo "--- login"
confere "API exige login" "$(codigo "$API/auth/me")" 401
resposta="$(req -D - -H 'Content-Type: application/json' -d "$(login_json "$EMAIL" "$SMOKE_PASSWORD")" "$API/auth/login")"
tem "login responde" "$resposta" "access_token"
if ! printf '%s' "$resposta" | grep -q access_token; then
  # Só nomes de campo e tipo do erro: a resposta inteira poderia repetir a senha enviada.
  echo "::error title=login recusado::$(printf '%s' "$resposta" | head -1 | tr -d '\r'); senha com ${#SMOKE_PASSWORD} caracteres; $(printf '%s' "$resposta" | grep -oE '"type":"[^"]+"|"loc":\[[^]]*\]' | tr '\n' ' ')"
fi
tem "cookie protegido" "$resposta" "httponly"
tem "cookie Secure em produção" "$resposta" "; secure"
token="$(printf '%s' "$resposta" | grep -oE '"access_token":"[^"]+"' | cut -d'"' -f4)"
cookie="$(printf '%s' "$resposta" | tr -d '\r' | grep -i '^set-cookie: mp_refresh=' | sed -E 's/^[^:]+: ([^;]+);.*/\1/')"
confere "token dá acesso" "$(codigo -H "Authorization: Bearer $token" "$API/stores")" 200
confere "renovação pelo cookie" "$(codigo -X POST -H "Cookie: $cookie" "$API/auth/refresh")" 200
confere "consulta da conexão com o ERP responde" "$(codigo -H "Authorization: Bearer $token" "$API/erp/connection")" 200

echo "--- limite de login e IP real"
alvo="alvo-$RANDOM@exemplo.com.br"
for i in 1 2 3 4 5; do
  # IP falso diferente a cada vez: se o nginx repassasse, o limite por conta + IP nunca chegaria.
  confere "erro $i com IP falsificado" "$(post_login "$alvo" senha-errada-123 -H "X-Forwarded-For: 203.0.113.$i")" 401
done
confere "falsificar o IP não escapa do limite" "$(post_login "$alvo" senha-errada-123 -H 'X-Forwarded-For: 203.0.113.99')" 429
confere "outro IP real não é bloqueado" "$(post_login "$alvo" senha-errada-123 --interface 127.0.0.2)" 401
confere "outra conta no mesmo IP não é bloqueada" "$(post_login "$EMAIL" "$SMOKE_PASSWORD")" 200

if [ "$falhas" -gt 0 ]; then echo "$falhas verificação(ões) falharam."; exit 1; fi
echo "Tudo certo."
