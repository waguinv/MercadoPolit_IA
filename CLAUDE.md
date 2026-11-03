# MercadoPilot AI — instruções para o Claude Code

Plataforma web/PWA multi-tenant para supermercados, integrada ao SysOrdem ERP por adaptador isolado.
Responda e documente em **português do Brasil**.

## Leia antes de qualquer alteração
1. `docs/MERCADOPILOT_AI_ARQUITETURA.md` — especificação primária (não invente decisões de negócio).
2. `docs/PROMPT_MESTRE_CLAUDE_CODE_MERCADOPILOT_AI.txt` — regras obrigatórias e ordem das etapas.
3. `docs/STATUS_DESENVOLVIMENTO.md` — o que já foi feito, verificado ou NÃO verificado, e a próxima etapa.
4. `docs/SYSORDEM_CONTRATOS.md` — o que o Swagger do SysOrdem confirma e o que falta homologar.

Confira o estado real (`git fetch`, `git status`, `git log --oneline -5`, testes) antes de presumir que algo foi feito.
Trabalhe a partir do `origin/main` atualizado: já houve duas sessões implementando a mesma etapa em paralelo.

## Modo de trabalho combinado com o dono do projeto
- Quando ele disser **"continue"**, avance para a próxima etapa pendente do `STATUS_DESENVOLVIMENTO.md`
  sem pedir aprovação de plano.
- Ao fim de cada etapa verificada: atualizar `STATUS_DESENVOLVIMENTO.md` e `README.md`, **fazer commit e push
  para `origin main` sem pedir confirmação**. Nunca usar force push; se o remoto divergir, parar e perguntar.
- Ainda exigem autorização explícita: escrever no ERP real, deploy, DNS, mudar a stack, apagar dados.
- Domínio de produção: `gestor.sysordemsistemas.com.br`. O servidor já atende outra aplicação: ver o status antes de implantar.

## Regras inegociáveis (resumo; o prompt mestre prevalece)
- Tenant e loja sempre aplicados **no servidor**; recurso de outra organização responde 404.
- Nenhuma chamada HTTP ao ERP fora de `backend/app/integrations/`. A porta do ERP é **somente leitura**.
- **Proibido** usar `POST /api/consulta/sql/v1` do SysOrdem e qualquer escrita no ERP sem homologação.
- Timeout/resposta ambígua = `integration_unknown`; reconciliar por leitura antes de repetir.
- Nunca somar contagens conflitantes; ausência de saldo no ERP não é zero; saldo absoluto não é movimento universal.
- Quantidades em `Decimal` com unidade explícita. Escritas idempotentes (`client_request_id`) e auditadas.
- Nunca pedir nem gravar senhas/tokens no chat, no Git ou em logs. Token do ERP só cifrado (`erp_connections`).
- Não criar botões ou telas decorativas que não funcionam. Nunca declarar teste/integração que não rodou.

## Comandos (Windows PowerShell, a partir da raiz do repositório)
```powershell
cd backend
python -m venv .venv                     # uma vez (Python 3.12+)
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
ruff check .                             # lint
pytest -q                                # testes (SQLite em memória; nunca tocam o ERP real)
$env:APP_ENV="test"; $env:ALEMBIC_DATABASE_URL="sqlite:///./mig_check.db"
alembic upgrade head; alembic check      # migrações; depois apague mig_check.db
```
Em Linux/macOS: `source .venv/bin/activate` e `export VAR=valor`.

PostgreSQL: defina `TEST_DATABASE_URL` (banco com `test` no nome). Sem ela, as corridas de `tests/test_concurrency_postgres.py` são
ignoradas; o CI roda as duas formas. Ao mexer em travas, confirme removendo-as numa branch temporária: as corridas precisam falhar.
Frontend (`cd frontend`): `npm install`, `npm test`, `npm run build` (inclui a checagem de tipos).
Sistema completo local: `backend/.venv/Scripts/python.exe scripts/dev_local.py` + `npm run dev` (dados fictícios, só SQLite).

## Checklist de cada etapa (critério de pronto)
1. Escopo da etapa conforme a arquitetura; implementar só ele.
2. Testes de regra de negócio, tenant, permissões, idempotência e falhas do ERP.
3. Quebrar de propósito as regras críticas e confirmar que algum teste falha; restaurar o código.
4. `ruff check .` limpo, `pytest -q` verde, `alembic upgrade head` + `alembic check` + downgrade/upgrade.
5. `git status`: nenhum `.env`, banco `.db`, token ou ZIP versionado.
6. Atualizar `STATUS_DESENVOLVIMENTO.md` separando **verificado** de **NÃO verificado**; commit; push.

## Estrutura
`backend/app/{api/v1,core,db,models,schemas,services,integrations/sysordem}` · `backend/tests` ·
`backend/alembic/versions` (uma migração por etapa: 0001…) · `docs/` · `scripts/` · `frontend/` (React + Vite; identidade visual SysOrdem em `src/tokens.css`, copiada do `sysordem-dashbord`: não inventar cores).
