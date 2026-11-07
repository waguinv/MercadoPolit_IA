#!/usr/bin/env bash
# Implanta ou atualiza o MercadoPilot a partir do código já baixado. Pode ser repetido; os dados são preservados.
# Uso: sudo ./implantar.sh
set -euo pipefail
dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
raiz="$(cd "$dir/../.." && pwd)"
FRONTEND_DIR="${FRONTEND_DIR:-/var/www/gestor}"
compose="$dir/compose.sh"

[ -f "$dir/.env.producao" ] || { echo "Falta $dir/.env.producao (copie de .env.example)."; exit 1; }
if grep -q 'troque-por-um-valor-gerado' "$dir/.env.producao"; then
  echo "O .env.producao ainda tem valores de exemplo."; exit 1
fi

# O disco é compartilhado: encher o servidor derrubaria os outros sistemas também.
livre_gb="$(df -BG --output=avail / | tail -1 | tr -dc '0-9')"
if [ "$livre_gb" -lt "${DISCO_MINIMO_GB:-4}" ]; then
  echo "Só há ${livre_gb} GB livres no disco; o mínimo é ${DISCO_MINIMO_GB:-4} GB. Nada foi alterado."
  exit 1
fi

echo "== Compilando o frontend e o backend"
novo="$(mktemp -d)"
trap 'rm -rf "$novo"' EXIT
docker build --quiet --target dist --output "type=local,dest=$novo" "$raiz/frontend"
[ -f "$novo/index.html" ] || { echo "Build do frontend não gerou index.html."; exit 1; }

# versao.json: o app avisa a pessoa da versão nova e do que mudou desde a anterior implantada aqui.
anterior="$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1])).get("commit", ""))' \
  "$FRONTEND_DIR/versao.json" 2> /dev/null || true)"
python3 - "$raiz" "$anterior" > "$novo/versao.json" <<'PY'
import json, subprocess, sys

raiz, anterior = sys.argv[1], sys.argv[2]


def git(*args):
    r = subprocess.run(["git", "-c", f"safe.directory={raiz}", "-C", raiz, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip()


_, atual = git("rev-parse", "HEAD")
mudancas = []
if anterior and atual and git("merge-base", "--is-ancestor", anterior, atual)[0] == 0:
    mudancas = git("log", "--no-merges", "--format=%s", f"{anterior}..{atual}")[1].splitlines()
if not mudancas:
    mudancas = [git("log", "-1", "--format=%s")[1]]
print(json.dumps({"versao": atual[:7] or "desconhecida", "commit": atual,
                  "data": git("log", "-1", "--format=%cI")[1],
                  "mudancas": [m for m in mudancas if m][:15]}, ensure_ascii=False))
PY
"$compose" build backend

echo "== Banco"
"$compose" up -d --wait db
if [ "$("$compose" exec -T db psql -U mercadopilot -d mercadopilot -Atc "select count(*) from information_schema.tables where table_name='alembic_version'")" = "1" ]; then
  echo "== Backup antes de migrar"
  "$dir/backup.sh"
fi

echo "== Migrações"
"$compose" run --rm backend alembic upgrade head

echo "== Backend"
"$compose" up -d --wait backend

# A interface só é trocada depois de o backend novo responder.
echo "== Interface"
chmod -R a+rX "$novo"
mkdir -p "$(dirname "$FRONTEND_DIR")"
rm -rf "$FRONTEND_DIR.novo" "$FRONTEND_DIR.antigo"
cp -a "$novo" "$FRONTEND_DIR.novo"
if [ -e "$FRONTEND_DIR" ]; then mv "$FRONTEND_DIR" "$FRONTEND_DIR.antigo"; fi
mv "$FRONTEND_DIR.novo" "$FRONTEND_DIR"
rm -rf "$FRONTEND_DIR.antigo"

echo "Implantado: $(git -C "$raiz" log -1 --format='%h %s' 2>/dev/null || echo 'versão desconhecida')"
