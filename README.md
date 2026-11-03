# MercadoPilot AI

Plataforma web/PWA para operações e inteligência de supermercados, multi-tenant, com integração
ao SysOrdem ERP por adaptador isolado. Especificação completa em [`docs/`](docs/).

> **Estado atual:** backend das Etapas 1, 2, 2b e 3 e frontend PWA do inventário. Não há escrita no ERP nem deploy.
> Veja [`docs/STATUS_DESENVOLVIMENTO.md`](docs/STATUS_DESENVOLVIMENTO.md) para o que foi verificado.

## Rodar localmente (SQLite, apenas para desenvolvimento rápido)
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp ../.env.example ../.env   # edite SECRET_KEY; para SQLite use DATABASE_URL=sqlite:///./dev.db
alembic upgrade head
python ../scripts/create_admin.py   # cria organização + 1º admin (senha por prompt)
uvicorn app.main:app --reload       # http://localhost:8000/docs
```
Com PostgreSQL via Docker Compose: `cp .env.example .env && docker compose up --build`
(**ainda não testado**, ver status).

## Rodar o sistema completo no computador
Precisa de Python e Node.js. Em dois terminais, a partir da raiz do repositório:
```bash
backend/.venv/Scripts/python.exe scripts/dev_local.py
```
```bash
cd frontend && npm install && npm run dev
```
Abra http://localhost:5173. O script sobe o backend na porta 8010 com SQLite (`backend/dev.db`), ERP simulado e usuários
fictícios; e-mails e senha de exemplo estão em [`scripts/dev_local.py`](scripts/dev_local.py). Ele recusa qualquer banco que não seja SQLite.
Para usar outro endereço de backend no frontend, defina `MP_API_URL`.

## Testes e lint
```bash
cd backend && ruff check . && pytest -q
```
```bash
cd frontend && npm test && npm run build
```
Os testes usam SQLite em memória e **nunca** tocam o ERP real.

## O que existe na Etapa 1
- Configuração por ambiente; `SECRET_KEY` forte obrigatória fora de dev/test.
- Modelos: `Organization`, `Store`, `User` (admin/manager/operator/auditor), `AuditEvent` (append-only no ORM).
- Autenticação JWT + senhas Argon2: `/api/v1/auth/login`, `/api/v1/auth/me`.
- Tenant derivado **no servidor** do usuário autenticado; o cliente nunca informa a organização.
- Lojas e usuários (`/api/v1/stores`, `/api/v1/users`), criação restrita a admin, com auditoria.
- Migração Alembic `0001`, Docker Compose, CI (GitHub Actions).

## O que existe na Etapa 2
- Catálogo espelhado do ERP (unidades e produtos) via `POST /api/v1/catalog/sync` (admin), consulta em `/units` e `/products`.
- Locais de estoque por loja; saldo lido do ERP em `/products/{id}/stock`.
- Adaptador de ERP **somente leitura** (`app/integrations/`): mock e SysOrdem (contrato do Swagger em
  [`docs/SYSORDEM_CONTRATOS.md`](docs/SYSORDEM_CONTRATOS.md)). **Ainda não testado contra o ERP real.**
- Conexão por organização (`/api/v1/erp/connection`): token cifrado no banco, nunca devolvido. Para usar o SysOrdem:
  `ERP_ADAPTER=sysordem`, `SECRETS_ENCRYPTION_KEY` (`python scripts/generate_secrets_key.py`) e o admin cadastra o token.
  Só em homologação e com autorização explícita; o endpoint `/api/consulta/sql/v1` do ERP é proibido no projeto.

## O que existe na Etapa 3
- Inventário colaborativo em `/api/v1/inventory-sessions`: sessões por local/setor, atribuição opcional, contagem cega,
  salvamento imediato com `client_key`, retomada (`my-counts`), reconferência por rodadas e decisão justificada.
- Contagens nunca são somadas. Divergência, duplicidade, unidade incompatível ou movimento no ERP levam o item à revisão.
- `erp-check` só **lê** o saldo. Encerrar a sessão aprova a contagem, mas **nada é enviado ao ERP**.
- Travas de concorrência ainda **não testadas em PostgreSQL** (ver status).
- Frontend PWA mobile-first (`frontend/`): login, sessões, contagem com fila no aparelho e revisão do gestor,
  com a identidade visual SysOrdem do `sysordem-dashbord` (ver status).

## Segurança
Nunca versionar `.env`, tokens ou credenciais SysOrdem. `ERP_ADAPTER=mock` é o padrão; `sysordem`
só após homologação e autorização explícita.
