#!/usr/bin/env bash
# Atualização automática: roda pelo agendamento do systemd (instalar_atualizacao_automatica.sh).
# Só implanta quando o main do GitHub avançou, o avanço é "fast-forward" e o CI desse commit passou.
# Commit com CI pendente: espera a próxima rodada. CI reprovado: não implanta e registra no log.
# Uso manual: sudo ./atualizar_automatico.sh   (log: journalctl -u mercadopilot-atualizar)
set -euo pipefail

# Responde "sucesso", "pendente" ou "falha" para o CI do commit. Sem resposta do GitHub: "pendente".
situacao_ci() {
  local resposta
  resposta="$(curl -fsS --max-time 20 -H 'Accept: application/vnd.github+json' \
    "$API/repos/$REPO/actions/runs?head_sha=$1&per_page=20")" || { echo pendente; return; }
  printf '%s' "$resposta" | python3 -c '
import json, sys
runs = json.load(sys.stdin).get("workflow_runs", [])
if not runs or any(r.get("status") != "completed" for r in runs):
    print("pendente")
elif all(r.get("conclusion") == "success" for r in runs):
    print("sucesso")
else:
    print("falha")'
}

# Tudo dentro de uma função: o "git merge" troca este arquivo, e o bash já terá lido o script inteiro.
main() {
  local dir raiz atual novo situacao
  dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  raiz="$(cd "$dir/../.." && pwd)"
  API="${MP_GITHUB_API:-https://api.github.com}"
  REPO="${MP_REPO:-$(git -C "$raiz" remote get-url origin | sed -E 's#^.*github\.com[:/]##; s#\.git$##')}"

  # Uma atualização por vez, mesmo que alguém rode à mão durante o agendamento.
  if command -v flock > /dev/null; then
    exec 9> "${MP_LOCK:-/run/lock/mercadopilot-atualizar.lock}"
    flock -n 9 || { echo "Outra atualização está em andamento."; return 0; }
  fi

  cd "$raiz"
  git fetch --quiet origin main
  atual="$(git rev-parse HEAD)"
  novo="$(git rev-parse origin/main)"
  [ "$atual" = "$novo" ] && return 0

  if ! git merge-base --is-ancestor "$atual" "$novo"; then
    echo "O servidor tem commits que não estão no GitHub ($atual). Nada foi alterado; resolva à mão."
    return 1
  fi

  situacao="$(situacao_ci "$novo")"
  case "$situacao" in
    pendente) echo "Versão ${novo:0:7} aguardando o CI."; return 0 ;;
    falha) echo "Versão ${novo:0:7} reprovada no CI: não foi implantada."; return 0 ;;
  esac

  echo "Atualizando de ${atual:0:7} para ${novo:0:7}."
  git merge --ff-only --quiet "$novo"
  if "$dir/implantar.sh"; then
    echo "Versão ${novo:0:7} implantada."
  else
    # Não repete sozinho: cada tentativa faz backup e compila, e o erro precisa ser visto por alguém.
    echo "FALHA ao implantar ${novo:0:7}. O código já está nesta versão; veja o log e rode implantar.sh à mão."
    return 1
  fi
}

main "$@"
exit $?
