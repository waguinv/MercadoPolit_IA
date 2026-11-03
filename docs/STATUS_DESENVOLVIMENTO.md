# Status de desenvolvimento

## Etapa 1 — Fundação: CONCLUÍDA PARCIALMENTE (backend verificado em SQLite)

### Verificado por execução (sandbox, Python 3.12, SQLite)
- `ruff check .` → sem erros.
- `pytest -q` → **23 passed** (login, tokens inválidos/expirados/forjados, usuário inativo,
  isolamento entre organizações, escopo por loja, permissões por papel, conflitos, auditoria
  append-only, remoção de segredos da auditoria, validação de configuração).
- Migração `0001`: `alembic upgrade head`, `downgrade base`, `upgrade head` e `alembic check` OK **em SQLite**.
- Smoke test real: migração + `scripts/create_admin.py` + uvicorn + login/me/criar loja via HTTP; sem token → 401.

### Escrito mas NÃO executado/verificado
- `docker-compose.yml` e `backend/Dockerfile` (sem Docker no sandbox).
- Migração e testes contra **PostgreSQL** (só no CI do GitHub Actions, que ainda não rodou).
- Workflow `.github/workflows/ci.yml` (nunca executado).
- Redis/Celery: ainda não usados.

### Decisões
- `User` tem `organization_id` + `role` + `store_id` opcional (restringe a uma loja). `Membership`/`Permission` separados ficam para quando um usuário precisar de várias lojas.
- Enum de papel gravado como VARCHAR (`native_enum=False`) para evitar migrações de tipo no Postgres.
- Recurso de outro tenant responde 404 (não 403) para não revelar existência.
- Auditoria append-only garantida no nível ORM (coberta por teste); trigger no banco fica para a Etapa 9.

### Pendências / riscos conhecidos
- Sem rate limiting/bloqueio de tentativas de login (Etapa 9). Sem refresh token/logout/revogação.
- Sem frontend (React/Vite/PWA), Redis/Celery ou Playwright.
- Sem endpoints para desativar usuário/loja nem trocar senha.
- Isolamento de tenant depende de filtros explícitos nos serviços; considerar RLS no PostgreSQL.
- Testes em SQLite podem divergir do PostgreSQL (concorrência, constraints).

## Etapa 2 — Cadastros e leitura do ERP via adaptador/mock: CONCLUÍDA (verificada em SQLite)

### Verificado por execução (sandbox, Python 3.12, SQLite)
- `ruff check .` sem erros; `pytest -q` → **52 passed** na época (hoje a suíte tem 93; ver Etapa 2b).
- Migração `0002`: upgrade, `alembic check`, downgrade para `0001` e novo upgrade OK **em SQLite**.
- Teste de mutação manual: removendo o filtro de organização em `/products` e removendo a validação
  dos dados do ERP, os testes falharam como esperado (código restaurado depois).
- Smoke HTTP real: sync do catálogo (3 unidades, 5 produtos), busca por nome e leitura de saldo (mock).

### O que foi entregue
- Modelos `UnitReference`, `ProductReference` (espelho do ERP; sem cadastro manual, evitando duplicidade) e `StockLocation`.
- Porta `ErpAdapter` **somente leitura** (`list_units`, `list_products`, `get_stock_balance`), `MockErpAdapter`, fábrica por `ERP_ADAPTER`. Teste de contrato reutilizável para o futuro adaptador SysOrdem; teste garante que a porta não tem métodos de escrita.
- `POST /api/v1/catalog/sync` (admin): valida dados do ERP antes de gravar, tudo-ou-nada, idempotente, desativa (nunca apaga) o que sumiu, auditado (sucesso e falha).
- `GET /units`, `GET /products` (busca, código de barras, paginação, `%`/`_` literais), `GET /products/{id}`.
- `GET /products/{id}/stock`: saldo lido do ERP, sem cache nem escrita; local exige `external_code`.
- Locais por loja: `GET/POST /stores/{id}/locations`, `PATCH /locations/{id}` (admin), respeitando tenant e restrição por loja.
- Erros do ERP mapeados: indisponível 503, timeout 504, demais 502 (inclusive quando levantados na fábrica).
- `ERP_ADAPTER=sysordem` responde 503 de propósito: **não há adaptador SysOrdem real**.

### Escrito mas NÃO verificado
- Tudo que depende de PostgreSQL, Docker e do CI (como na Etapa 1). Nenhuma chamada ao SysOrdem real foi feita ou simulada com base em contrato confirmado.

### Decisões e riscos
- O mock devolve o mesmo catálogo para todas as organizações; no SysOrdem real haverá credenciais por organização (a definir).
- Sincronização é síncrona na requisição; para catálogos grandes deve virar tarefa (Celery) com `IntegrationJob`.
- O sync assume que o ERP devolve a lista COMPLETA; paginação parcial exige que o adaptador real levante erro (documentado na porta).
- Não há UI para mapear local ↔ código ERP além do PATCH.
- Custos de produto não são importados (fonte ainda não confirmada).

## Etapa 2b — Adaptador SysOrdem real (somente leitura) + conexão por organização: CONCLUÍDA (verificada com HTTP simulado)

Motivo: o cliente enviou o `swagger.json` oficial. Ele mudou premissas (token por empresa, rate limit, ausência
de idempotência, módulo de inventário nativo). Detalhes e perguntas de homologação: `docs/SYSORDEM_CONTRATOS.md`.

### Verificado por execução (sandbox, SQLite, transporte HTTP simulado — NENHUMA chamada real ao SysOrdem)
- `ruff check .` sem erros; `pytest -q` → **93 passed**.
- Migração `0003` (`erp_connections`): upgrade, `alembic check`, downgrade e upgrade OK **em SQLite**.
- Mutação manual de 3 propriedades críticas (conexão de outra organização, validação de id no caminho da URL,
  token exposto na resposta): os testes falharam em todas; código restaurado.

### Entregue
- `SysordemErpAdapter` (`app/integrations/sysordem/adapter.py`): só `GET`; header `Authentication {token}`;
  paginação por `hasNext`; 429 com espera/retry só para leitura; timeout/rede/401/404/5xx/JSON inválido mapeados;
  sem redirecionamentos; token fora de mensagens de erro; ids validados como inteiros; `Decimal(str(double))`;
  saldo ausente/inválido = erro (nunca zero).
- Porta ampliada (somente leitura): `check_connection`, `list_stock_locations`; saldo devolve `reserved_quantity`
  e `last_changed_at`; unidade do saldo vem do espelho local.
- `ErpConnection`: token por organização **cifrado (Fernet)**, nunca devolvido nem auditado.
  `PUT/GET/DELETE /api/v1/erp/connection`, `POST /api/v1/erp/connection/test`, `GET /api/v1/erp/stock-locations`.
- `ERP_ADAPTER=sysordem`: cada organização usa o próprio token; sem conexão ativa → 503. URL base só do ambiente.
- Sync: produto sem unidade/com unidade desconhecida é **ignorado e reportado** (`products_skipped`), sem derrubar o sync nem desativar o já espelhado.
- Erros: 429 (limite do ERP), 502 (credencial rejeitada), 422 (pedido inválido), 503, 504.
- `.env.example`: removidos `SYSORDEM_CLIENT_ID/SECRET` (inexistentes nessa API); adicionados `SECRETS_ENCRYPTION_KEY` etc.

### NÃO verificado
- Nenhuma chamada ao SysOrdem real (sem token, sem homologação). Formatos reais de resposta, limite de `limit`,
  fuso de `dataHora`, comportamento sob 429 real: só conferidos contra o Swagger, não contra o servidor.
- PostgreSQL/Docker/CI (pendente desde a Etapa 1). Um token de teste pode ser cadastrado só por um admin via API,
  nunca pelo chat.

### Riscos / pendências
- Sync grande é lento por causa do rate limit e roda dentro da requisição → precisa de tarefa em segundo plano.
- `SECRETS_ENCRYPTION_KEY`: perder/trocar invalida tokens cifrados; sem rotação de chave implementada.
- Sem rate limiter próprio compartilhado entre processos (o limite é por token; vários workers podem estourá-lo).
- Repositório GitHub indicado (`waguinv/MercadoPolit_IA`) está **vazio e público**; nada foi enviado (sem credenciais aqui).

## Próximo passo
1. Cadastrar um token de **homologação** (via `PUT /api/v1/erp/connection`) e validar as leituras no ERP real; responder as perguntas da seção 5 de `docs/SYSORDEM_CONTRATOS.md`.
2. Publicar o repositório e rodar o CI/Docker em PostgreSQL (pendente desde a Etapa 1).
3. Etapa 3: inventário colaborativo na plataforma (sessões, contagens, reconferência, auditoria), ainda sem escrita no ERP; a aplicação no ERP usará o módulo de inventário nativo, após homologação.
