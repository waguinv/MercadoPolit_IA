# MercadoPilot AI

Plataforma web/PWA para operações e inteligência de supermercados, multi-tenant, com integração
ao SysOrdem ERP por adaptador isolado. Especificação completa em [`docs/`](docs/).

> **Estado atual:** Etapas 1 (Fundação) e 2 (Cadastros e leitura do ERP via mock) do backend. Não há frontend, integração ERP nem deploy.
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

## Testes e lint
```bash
cd backend && ruff check . && pytest -q
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
- Adaptador de ERP **somente leitura** (`app/integrations/`), com mock. O adaptador SysOrdem real não existe ainda.

## Segurança
Nunca versionar `.env`, tokens ou credenciais SysOrdem. `ERP_ADAPTER=mock` é o padrão; `sysordem`
só após homologação e autorização explícita.
