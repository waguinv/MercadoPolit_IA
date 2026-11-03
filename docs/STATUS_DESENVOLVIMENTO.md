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

## Etapa 3 — Inventário colaborativo (backend, sem escrita no ERP): CONCLUÍDA (verificada em SQLite)

### Verificado por execução (Windows, Python 3.13, SQLite, ERP simulado — NENHUMA chamada real ao SysOrdem)
- `ruff check .` sem erros; `pytest -q` → **155 passed** (62 novos em `tests/test_inventory.py`).
- Migração `0004`: `upgrade head`, `alembic check`, `downgrade 0003`, novo `upgrade` e `check` OK **em SQLite**.
- Mutação manual de 8 regras críticas (organização, loja, contagens conflitantes, contagem cega, unidade errada
  na decisão, `client_key`, encerrar com pendência, margem do horário do ERP): os testes detectaram **todas**; código restaurado.

### Entregue
- Modelos: `InventorySession`, `InventoryAssignment`, `InventoryItem`, `InventoryCount`, `InventoryRecount`,
  `InventoryErpCheck`. Contagem, reconferência e leitura do ERP são **append-only** no ORM.
- Rotas em `/api/v1/inventory-sessions`: criar/listar/detalhar, `submit-review`, `close`, `cancel`, `assignments`,
  `counts`, `my-counts`, `pending-recounts`, `items`, `items/{id}`, `recounts`, `items/{id}/resolve`, `items/{id}/erp-check`.
- Estados da sessão: `open` → `in_review` → `closed`; `open`/`in_review` → `cancelled`.
  Estados do item: `counted`, `needs_review`, `recount_requested`, `resolved`.
- Vão para revisão: `count_conflict`, `duplicate_count`, `unit_mismatch`, `fraction_not_allowed`,
  `count_after_resolution`, `erp_difference`, `erp_movement_after_count`, `erp_movement_unverifiable`.

### Decisões
- **`closed` significa contagem aprovada, não saldo aplicado.** Nada é enviado ao ERP nesta etapa.
- **Contagem cega:** operador não vê saldo do ERP nem contagens alheias nas rotas de inventário. Vê só as próprias
  (`my-counts`) e as reconferências pendentes, sem quantidades.
- **Nunca soma nem faz média.** A decisão escolhe uma contagem real da rodada atual, com justificativa; não aceita número digitado.
- **Produto não contado não vira item** e não significa saldo zero.
- **Salvar a contagem não depende do ERP** (limite de 20 req/min). O ERP só é lido por item, sob demanda do gestor.
- `client_key` é obrigatória: reenvio após falha de rede devolve o registro original (200), sem duplicar. A chave é por usuário.
- Unidade errada é **salva e sinalizada**, nunca convertida; essa contagem não pode ser aceita, só reconferida.
- O ERP não informa o fuso de `dataHora`. Horário sem fuso é tratado com **margem de 5h** (Brasil até UTC-5): pode
  sinalizar movimento que ocorreu antes da contagem, mas não deve deixar passar um posterior. Sem horário = `erp_movement_unverifiable`.
- Uma sessão ativa por local/setor; sessão do local inteiro bloqueia a de qualquer setor desse local.
- `InventoryDiscrepancy` do modelo conceitual não virou tabela: a divergência é representada por `review_reasons` + histórico.

### Escrito mas NÃO verificado
- **Travas de concorrência em PostgreSQL** (`FOR UPDATE`/`FOR SHARE` e a nova tentativa após `IntegrityError`): o SQLite
  ignora essas travas e os testes rodam em sequência; não há Docker nem PostgreSQL nesta máquina. Corridas de contagem
  são o principal risco em aberto da etapa.
- Migração `0004` em PostgreSQL, Docker e CI (pendentes desde a Etapa 1).

### Riscos / pendências
- **Sem frontend:** o inventário só é utilizável pela API; nenhum operador consegue usá-lo no celular ainda.
- **Aplicação no ERP não existe.** Depende das perguntas 3 e 4 da seção 5 de `docs/SYSORDEM_CONTRATOS.md`.
- O mesmo produto em dois setores de um local gera dois itens em duas sessões. Como somar na aplicação ao ERP ainda precisa de
  regra de negócio; a contagem por setor não fecha isso sozinha.
- `GET /products/{id}/stock` continua aberto a operadores, o que **enfraquece a contagem cega**. Restringir é decisão de negócio.
- A leitura do ERP é opcional: uma sessão pode ser encerrada sem nenhum `erp-check`. A releitura será obrigatória na aplicação.
- Gestor pode decidir sobre a própria contagem (a segregação da especificação vale só para consumo interno); fica na auditoria.
- A margem de 5h deve sinalizar muitos itens em horário de loja até o fuso ser confirmado em homologação.

## Etapa 3b — Frontend PWA do inventário: CONCLUÍDA (verificada no navegador, backend em SQLite, ERP simulado)

### Verificado por execução
- `npm test` → **13 passed**; `npm run build` (com `tsc --noEmit`) sem erros; `ruff` e `pytest` do backend passando.
- Fluxo real no navegador, em tela de celular (375×812), contra o backend local:
  - Gerente: login, criar sessão, contar, consultar saldo no ERP simulado, aceitar contagem com justificativa, enviar para revisão e encerrar.
  - Operadora: login pelo formulário, sem aba de revisão (API responde 403), busca por código de barras indo direto à quantidade,
    fração recusada em unidade UN, reconferência pedida pelo gerente aparecendo e sendo feita (rodada 2).
  - Duas contagens diferentes do mesmo produto foram para revisão, sem soma.
  - Backend desligado: contagem guardada no aparelho, mantida após recarregar a página e enviada ao religar, **sem duplicar**.
  - Login expirado com contagem pendente: nada perdido; depois de entrar de novo, a pessoa volta à mesma tela e a contagem é enviada.
  - Servidor web desligado (porta confirmada livre): a interface abre pelo service worker com os dados salvos.

### Entregue
- `frontend/`: React + TypeScript + Vite, PWA (manifesto e `public/sw.js`, que nunca guarda respostas da API), interface em português do Brasil.
- Fila de contagens por usuário (`src/lib/countQueue.ts`): grava antes de enviar, reenvia com a mesma `client_key`; recusa definitiva
  fica visível, e só a própria pessoa pode descartar.
- Backend: `GET /inventory-sessions/{id}/assignable-users`, nome de quem contou na revisão e `product_name`/`unit_id` para a tela de contagem.
- `scripts/dev_local.py` (dados fictícios, só SQLite) e job de frontend no CI.

### NÃO verificado
- Aparelho físico, instalação do PWA e câmera. O CI continua sem rodar. Não há testes Playwright no repositório: o roteiro acima foi executado uma vez, à mão.
- Tema escuro e tela de computador: só o CSS foi escrito.
- Timeout de 15s em rede que trava: implementado depois do roteiro, sem teste de rede lenta.

### Riscos / pendências
- **Token em `localStorage`** para sobreviver ao fechamento do app: um XSS poderia roubá-lo. O login dura só 30 minutos e **não há refresh token**;
  numa contagem longa a pessoa precisará entrar de novo várias vezes.
- **Sem telas de configuração:** lojas, locais e sincronização do catálogo ainda exigem a API (ou o script local). Uma organização nova não começa só pela interface.
- Sem leitura de código de barras pela câmera; só digitação ou leitor físico que digita.
- Sem rede, a busca só encontra produtos já consultados naquele aparelho (até 500). O catálogo não é baixado para uso offline.
- Dados salvos no aparelho (contagens pendentes e cópias de consultas) continuam nele ao sair do login; o aparelho compartilhado separa por usuário, mas não cifra.
- HTTP na rede local não ativa o service worker: o PWA exige HTTPS em produção.

## Próximo passo
1. Cadastrar um token de **homologação** (via `PUT /api/v1/erp/connection`) e validar as leituras no ERP real; responder as perguntas da seção 5 de `docs/SYSORDEM_CONTRATOS.md`.
2. Publicar o repositório e rodar o CI/Docker em PostgreSQL, incluindo um teste de corrida das contagens (pendente desde a Etapa 1).
3. Testar em celular real com a equipe; depois, resolver o login de 30 minutos e as telas de configuração.
4. Etapa 4: recebimento.
