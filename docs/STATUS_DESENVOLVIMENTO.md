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

## Etapa 3 — Inventário colaborativo (sem escrita no ERP): CONCLUÍDA (verificada em SQLite + HTTP simulado)

### Verificado por execução (sandbox, Python 3.13, SQLite, ERP simulado)
- `ruff check .` sem erros; `pytest -q` → **122 passed** (96 anteriores + 26 do inventário).
- Teste de mutação manual de 6 regras críticas (somar contagens, ausência de saldo = zero, aceitar contagem
  obsoleta, sem filtro de organização, finalizar com divergência aberta, operador vendo contagens alheias):
  os testes falharam em todas; código restaurado byte a byte.
- Migração `0004` gerada com autogenerate (inclui índice parcial único `uq_inventory_active_location`).

### Fluxo
`POST /inventory-sessions` (lê o saldo do ERP = *baseline*) → `POST /{id}/counts` (salva na hora, idempotente) →
`POST /{id}/close-counting` (relê o ERP e gera divergências) → `POST /{id}/discrepancies/{d}/resolve` ou
`POST /{id}/recounts` → `POST /{id}/finalize` (gera **propostas** de ajuste) / `POST /{id}/cancel`.
Leituras: `GET /inventory-sessions[/{id}|/{id}/counts|/{id}/discrepancies|/{id}/proposals]`.

### Regras implementadas (todas testadas)
- **Nunca soma contagens**: cada contador vale pela última contagem própria; contadores iguais = consenso (não soma);
  diferentes = divergência `conflicting_counts`, resolvida só escolhendo UM dos valores contados.
- **Ausência de saldo no ERP ≠ zero** (`no_erp_balance`).
- **Saldo do ERP mudou depois da abertura** (`erp_changed_since_baseline`): contagem antiga não pode ser aceita;
  só manter o ERP ou recontar (a recontagem relê o ERP e cria novo baseline da rodada).
- Divergência `differs_from_erp` e `no_erp_balance` exigem decisão humana com nota (mín. 5 caracteres).
- Uma sessão ativa por local (índice parcial único). Leitura do ERP falhou ⇒ nada é criado/alterado.
- Contagem idempotente por `(sessão, usuário, client_request_id)`; reuso com dados diferentes = 409.
- Quantidade: Decimal, ≥ 0, até 3 casas; unidade sem fração rejeita fração; unidade guardada com a contagem.
- Contagens e baselines são append-only (guarda no ORM, testada). Decisões e fechamentos são auditados.
- Papéis: admin/gerente criam, fecham, resolvem, recontam, finalizam, cancelam; operador só conta;
  auditor só lê. **Contagem às cegas**: operador não vê saldo do ERP, divergências nem contagens de outros.
- Tenant e loja aplicados no servidor (404 fora do escopo).
- **Nenhuma escrita no ERP**: o resultado são `InventoryAdjustmentProposal` (status `proposed`) e a sessão fica com
  `erp_application_status = "not_applied"`. Um teste confere que o saldo simulado do ERP não muda.
- Porta do ERP ganhou `list_location_balances` (uma leitura paginada do local; evita 1 chamada por produto sob o
  limite de 20 req/min). Implementada no mock e no adaptador SysOrdem (testes com HTTP simulado).

### NÃO verificado
- PostgreSQL: o índice parcial, `SELECT … FOR UPDATE` (serialização contagem × fechamento) e a concorrência real
  só foram exercitados em SQLite, que ignora o bloqueio de linha. O CI do GitHub roda o PostgreSQL; **teste de
  concorrência real (duas contagens simultâneas vs. fechamento) ainda não existe**.
- `GET /api/local-estoque/{id}/estoques/v1` contra o ERP real: só conferido no Swagger. Dúvidas: o ERP lista
  produtos com saldo zero ou sem registro? Retorna todas as variações do local?
- Nenhum frontend: o fluxo de contagem pelo celular (PWA) ainda não existe.

### Limitações / riscos conhecidos
- Aplicar o resultado no ERP **não existe**: exige homologar o inventário nativo (`/api/inventario/...`) e a
  pergunta aberta sobre o encerramento sobrescrever movimentos posteriores (ver `docs/SYSORDEM_CONTRATOS.md`).
- Sessão de um local grande lê muitas páginas do ERP (limite 20 req/min) dentro da requisição; deve virar tarefa.
- Desempate de duas contagens do mesmo usuário usa `counted_at` (microssegundos); em relógios de baixa resolução
  duas contagens no mesmo instante teriam ordem indefinida.
- Contagem de lote/validade e múltiplos setores por sessão ainda não existem; produtos só do ERP e inativos não
  entram. Sem rate limiting próprio.
- Atribuição opcional de contadores a setores (previsto na arquitetura) não foi implementada.

## Etapa 4 — Recebimento manual (sem escrita no ERP): CONCLUÍDA (verificada em SQLite)

### Verificado por execução (sandbox, Python 3.13, SQLite)
- `ruff check .` sem erros; `pytest -q` → **138 passed** (122 anteriores + 16 do recebimento).
- Teste de mutação manual de 8 regras críticas (aceito+rejeitado=recebido, vencido aceito, filtro de organização,
  edição fora do rascunho, cancelar finalizado, cálculo de falta, produto+lote duplicado, finalizar sem itens).
  Resultado: 7 foram pegas de primeira; a 8ª (finalizar sem itens) **sobreviveu** porque o teste só conferia o
  status 422 (a lista vazia cai em outra regra com o mesmo status). O teste foi reforçado para conferir a mensagem
  e a mutação passou a ser pega. Código restaurado byte a byte.
- Migração `0005` gerada com autogenerate (inclui 2 índices únicos parciais: chave de acesso e
  fornecedor+série+número, ambos ignorando recebimentos cancelados).

### Fluxo
`POST /receivings` (rascunho) → `POST /{id}/items` (várias vezes; `PATCH`/`DELETE` enquanto rascunho) →
`POST /{id}/finalize {partial}` (gerente) ou `POST /{id}/cancel` (gerente). Leitura: `GET /receivings[/{id}]`.

### Regras implementadas (testadas)
- Sem pedido obrigatório; fornecedor em texto livre + CPF/CNPJ opcional (11/14 dígitos); chave de acesso opcional
  validada (44 dígitos + DV módulo 11). **Não há importação de XML** (API de entrada por XML não confirmada).
- NF-e duplicada (mesma chave) e mesmo fornecedor+série+número são recusados (409) enquanto o recebimento
  anterior não estiver cancelado.
- Item: recebido, aceito, rejeitado (motivo: avaria, item incorreto, vencido, qualidade, outro), lote, validade,
  quantidade esperada opcional. **Aceito + rejeitado = recebido**; rejeição exige motivo; **produto vencido não
  pode ser aceito**; unidade sem fração rejeita fração; no máximo 3 casas decimais.
- **Falta e excesso são calculados** (esperado × recebido), nunca digitados.
- Parcial: `partial=true` na finalização marca as faltas como pendentes (o registro das quantidades esperadas
  permanece); `partial=false` fecha as faltas como definitivas.
- Só rascunho é editável. Finalizado **não é cancelado nem excluído** (cancelar exigiria movimento compensatório,
  que depende do ERP). Criação e itens idempotentes por `client_request_id` (reuso com dados diferentes = 409).
- Papéis: admin/gerente/operador registram; só admin/gerente finalizam e cancelam; auditor só lê.
- Tenant e loja aplicados no servidor (404 fora do escopo). Auditoria de criação, itens, finalização, cancelamento.
- **Nenhuma escrita no ERP**: `erp_application_status = "not_applied"`.

### NÃO verificado / limitações
- PostgreSQL (índices parciais, trava `FOR UPDATE`, concorrência) só foi exercitado em SQLite.
- **Finalizar não atualiza o estoque do ERP.** Não existe contrato confirmado de entrada de mercadoria
  (o `POST /api/documento/v1` aceita PA/PV/OC/CN sem efeito documentado). Hoje o recebimento é um registro de
  conferência auditável; levar a entrada ao ERP exige homologação (ver `docs/SYSORDEM_CONTRATOS.md`).
- Não há anexos/fotos de avaria, importação de XML, vínculo com pedido de compra, custo, nem cadastro de
  fornecedor (o ERP tem `/api/pessoa`, ainda não espelhado). Não há frontend/PWA.
- `received_at` aceita data informada pelo cliente (usada na regra de validade); sem limite de retroatividade.
- Item com quantidade recebida zero é permitido (registra falta), mas finalizar exige ao menos um item recebido.

## Próximo passo
1. Rodar o CI no GitHub (PostgreSQL) e escrever teste de concorrência real em PostgreSQL.
2. Com token de **homologação**, validar as leituras e os pontos abertos de `docs/SYSORDEM_CONTRATOS.md`.
3. Etapa 5: Central de Movimentações (perdas/avarias, consumo interno com aprovação e segregação, devoluções,
   transferências), ainda sem escrita no ERP real.
