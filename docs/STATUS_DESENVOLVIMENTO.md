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
- Docker (pendente desde a Etapa 1). As travas e a migração em PostgreSQL já foram verificadas: ver "Testes em PostgreSQL".

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
- Tela de computador: só a lista de sessões foi aberta nessa largura, sem conferir a aparência.
- Timeout de 15s em rede que trava: implementado depois do roteiro, sem teste de rede lenta.

### Riscos / pendências
- Login e telas de configuração foram resolvidos depois: ver "Login e configuração".
- Sem leitura de código de barras pela câmera; só digitação ou leitor físico que digita.
- Sem rede, a busca só encontra produtos já consultados naquele aparelho (até 500). O catálogo não é baixado para uso offline.
- Dados salvos no aparelho (contagens pendentes e cópias de consultas) continuam nele ao sair do login; o aparelho compartilhado separa por usuário, mas não cifra.
- HTTP na rede local não ativa o service worker: o PWA exige HTTPS em produção.

## Identidade visual SysOrdem (padrão dos apps)

Fonte: repositório `waguinv/sysordem-dashbord`, commit `7c1f7ea`. Vale para todos os módulos do MercadoPilot.
- **Copiados sem alteração:** `frontend/src/tokens.css`, logos em `frontend/src/assets/brand/`, favicon e ícones em `frontend/public/`.
  Para mudar a marca, atualize no projeto de origem e copie de novo; não invente cores aqui.
- **Reproduzidos em `frontend/src/styles.css`:** cabeçalho, login, cartões, botões, abas, campos, etiquetas e animação de carregando.
- **Regras seguidas, como citadas no código de origem:** Arial; símbolo abaixo de 180 px e logo vertical acima disso; versão reversa em fundo escuro; logo sem
  distorção nem efeitos. O Manual da Marca (`Kit-SysOrdem-2026`) não está no repositório e **não foi consultado**.
- **Decisões:** nome do app "SysOrdem MercadoPilot" (curto: "MercadoPilot"); o tema escuro foi removido, pois o padrão não tem.
- **Não copiado:** barra de navegação inferior (com um único módulo seria decorativa; entra com a Etapa 4) e gráficos.
- Conferido em tela de celular: login, lista de sessões, revisão e item. Tela de computador e ícone do app instalado não foram conferidos.

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
  fornecedor (o ERP tem `/api/pessoa`, ainda não espelhado). As telas vieram depois: ver "Telas do recebimento".
- `received_at` aceita data informada pelo cliente (usada na regra de validade); sem limite de retroatividade.
- Item com quantidade recebida zero é permitido (registra falta), mas finalizar exige ao menos um item recebido.

## Junção de duas linhas de trabalho (2026-11-03)

Duas sessões implementaram a Etapa 3 em paralelo. Por decisão do dono do projeto, ficou o **inventário da linha local**, que
é o usado pelo frontend. Da outra linha vieram a Etapa 4 (recebimento), o `CLAUDE.md` e `list_location_balances` na porta do ERP.

- **Saiu da árvore:** o outro inventário (saldo de referência na abertura, divergências e propostas de ajuste). Continua no histórico
  do Git, no commit `301cb6a`, se alguma ideia for aproveitada. Os números de teste citados na Etapa 4 (138) são dessa outra suíte.
- `list_location_balances` só é usada pelos testes hoje; foi mantida para a futura leitura de saldos por local.
- **O recebimento ainda não tem telas**: só API.

### Verificado por execução depois da junção (Windows, Python 3.13, SQLite, ERP simulado)
- `ruff check .` sem erros; `pytest -q` → **176 passed**; `npm test` → **13 passed**; `npm run build` sem erros.
- Migrações: uma única sequência (`0001`…`0005`); `upgrade head`, `check`, `downgrade 0003`, novo `upgrade` e `check` OK em SQLite.
- No navegador, contra o backend juntado: contagem e consulta ao ERP simulado pela interface. Recebimento pela API: criar, incluir item
  (10 recebidos, 9 aceitos, 1 rejeitado por avaria) e finalizar, resultando em `finalized` e `erp_application_status = not_applied`.
- A mutação manual de regras não foi repetida depois da junção; o código das duas partes não mudou.

## Testes em PostgreSQL

Esta máquina não tem PostgreSQL: a verificação acontece no CI do GitHub (PostgreSQL 16). Agora o CI roda a suíte duas vezes,
em SQLite e em PostgreSQL. Para rodar em outro lugar, defina `TEST_DATABASE_URL`; o banco precisa ter `test` no nome, porque as tabelas são apagadas.

### Verificado por execução
- **CI passou** (execução 41699221434): suíte completa e 14 corridas reais entre conexões em `tests/test_concurrency_postgres.py`.
  O número total de testes não foi conferido, porque os registros do CI exigem login. Localmente, em SQLite: 176 passam e as 14 corridas são ignoradas.
- **Prova de que os testes detectam o problema:** com as 5 travas removidas numa branch temporária (execução 41699263937, branch apagada),
  **8 das 14 corridas falharam**, entre elas: contagem divergente aceita em silêncio como saldo, contagem gravada depois do cancelamento,
  item incluído depois da finalização, 6 sessões simultâneas no mesmo local e 4 finalizações do mesmo recebimento.
- Na primeira versão, 3 corridas passavam mesmo sem travas. Foram reescritas para cobrir o caso em que só a trava protege.

### Defeito encontrado e corrigido
- **Recebimento:** dois envios simultâneos com o mesmo `client_request_id` faziam um deles receber 409 "nota já registrada", em vez do registro
  original. Agora devolve o original. Só apareceu em PostgreSQL.

### NÃO verificado
- As corridas chamam os serviços diretamente, com 10 rodadas cada: passar reduz o risco, mas não prova ausência de corrida. Não testam a API sob carga real.
- `erp-check` concorrente, edição e remoção concorrentes de itens do recebimento e Docker não têm teste.

## Login e configuração

### Entregue
- **Login contínuo:** token de acesso de 30 min, só na memória, renovado sem a pessoa notar. O de renovação (14 dias sem uso, `REFRESH_TOKEN_EXPIRE_DAYS`) fica em cookie
  `HttpOnly` + `SameSite=Strict` (`Secure` fora de desenvolvimento), com só o hash no banco (`refresh_tokens`, migração `0006`). Nenhum token fica no `localStorage`.
- **Rotação com detecção de roubo:** cada uso troca o token; reuso depois de 60 s revoga o login inteiro e é auditado. Até 60 s é tolerado, para rede instável e duas abas.
- `POST /auth/refresh`, `/auth/logout`, `/auth/change-password`; `PATCH /users/{id}` (ativar/desativar, redefinir senha). Trocar senha ou desativar encerra os aparelhos; o admin não desativa a si mesmo.
- **Telas:** Configuração (só admin): conexão do ERP, catálogo, lojas e locais com mapeamento ao ERP, usuários. Minha conta: trocar a própria senha. Menu inferior do padrão SysOrdem.

### Verificado por execução
- Backend em SQLite: `ruff` limpo, **194 passed** e 15 corridas ignoradas (só rodam no CI). Frontend: **20 passed**, build sem erros. Migração `0006` sobe, desce e sobe.
- No navegador, em tela de celular, com ERP simulado, a partir de uma **organização nova e vazia** criada por `scripts/create_admin.py`: testar conexão, sincronizar catálogo, criar loja,
  local mapeado e operadora, recarregar a página continuando logado, criar sessão, sair (renovação passa a responder 401), entrar como operadora (sem menu de Configuração, rota redireciona), contar e trocar senha.
- `document.cookie` vazio e nenhum token no `localStorage`.

### NÃO verificado
- **Vencimento real de 30 min no navegador:** a renovação automática foi testada em testes unitários do frontend e ao recarregar a página, sem esperar o token expirar.
- Formulário de token do ERP real (`ERP_ADAPTER=sysordem`) não foi usado na interface. Desativar usuário, redefinir senha e editar local têm teste de API, mas não foram clicados na tela.
- O cookie `Secure` exige HTTPS; nunca rodou atrás de HTTPS.
- Reuso de token só detecta roubo se o token antigo for usado depois da troca.

### Riscos / pendências
- Limite de tentativas de login foi resolvido depois: ver "Limite de tentativas de login".
- Depois de trocar a senha ou desativar, o token de acesso antigo pode valer até 30 min. Em desativação o servidor já recusa na hora; em troca de senha, não.
- **Primeiro administrador** ainda nasce por `scripts/create_admin.py`: criar uma empresa nova não tem tela.
- Sem recuperação de senha por e-mail: quem esquece depende do admin; o último admin depende do script.
- Tela não permite editar nome, papel ou loja de um usuário já criado.

## Limite de tentativas de login

Tabela `login_throttles` (migração `0007`), com a chave em hash; a tentativa é reservada sob trava **antes** de conferir a senha. Janela fixa de 15 min, com limites configuráveis:

| Limite | Máximo | Motivo |
|---|---|---|
| Conta + IP | 5 | quem erra não atrapalha a mesma pessoa em outro lugar |
| Conta | 20 | ataque distribuído contra uma pessoa |
| IP | 50 falhas | muitas contas a partir de um lugar; acertos não contam, pois uma loja inteira pode sair pelo mesmo IP |

Trocar a própria senha também tem limite (5 erros). Bloqueado responde 429 com `Retry-After`, sem conferir a senha e sem escrever na auditoria; atingir o limite é auditado uma vez.
Quem já está logado não cai: a renovação não passa pelo limite.

### Verificado por execução
- Em SQLite: `ruff` limpo, **210 passed** e 16 corridas ignoradas. Migração `0007` sobe, desce e sobe.
- **CI passou** com as 16 corridas em PostgreSQL (execução 41705578224). Lote de 20 logins simultâneos: exatamente 5 senhas conferidas em cada uma das 10 rodadas.
- **Prova:** sem a trava (execução 41705633644, branch apagada), as **20 tentativas foram conferidas**, com limite de 5.
- No navegador, com o backend local: 5 erros mostram "Credenciais inválidas"; a sexta tentativa e a senha certa em seguida mostram "Muitas tentativas. Aguarde 15 minuto(s)"; outra conta entra normalmente.

### NÃO verificado
- Atrás de proxy real (IP encaminhado). Desbloqueio após 15 min foi testado alterando o horário no banco, sem esperar. O bloqueio na troca de senha só tem teste de API.

### Decisões e riscos
- **Implantação atrás de proxy:** o uvicorn precisa de `--forwarded-allow-ips=<ip do proxy>`. Sem isso, todos parecem um só IP, e 50 falhas de qualquer pessoa bloqueiam **todo mundo**. Conferir antes da produção.
- **Bloqueio proposital:** com 20 tentativas por 15 min, alguém pode impedir um novo login de uma pessoa. É o preço do limite por conta; quem já está logado segue trabalhando.
- O administrador não tem botão para liberar um bloqueio: redefinir a senha não o limpa, e é preciso esperar a janela.
- Ataque de muitos IPs contra muitas contas, com poucas tentativas em cada, não é barrado aqui; isso exige proteção na borda (proxy/CDN).
- Sem CAPTCHA nem exigência de senha forte além do tamanho mínimo.

## Telas do recebimento

Lista, novo recebimento, lançamento e edição de itens, finalização e cancelamento, em `frontend/src/features/receiving/`, com as regras em `frontend/src/lib/receiving.ts`. Os itens da API passaram a trazer `product_name`. Sem migração nova.

### Verificado por execução (Windows, SQLite, ERP simulado, tamanho de celular)
- `ruff check .` limpo; `pytest -q` com 211 aprovados e 16 corridas ignoradas (sem PostgreSQL local); `alembic upgrade head` e `alembic check` sem pendência; `npm test` com 39 aprovados; `npm run build` limpo.
- **No navegador, como operador:** chave de acesso inválida recusada; criação; busca por nome e por código de barras; aceito calculado na hora; fração recusada em unidade `UN`; item com falta; edição que zera a rejeição; produto vencido recusado como aceito e aceito como rejeitado; sem botões de finalizar e cancelar.
- **Como gerente:** finalização só libera depois de escolher o destino das faltas; entrega parcial registrada; finalizado sem botões de edição; nota repetida recusada pelo servidor com mensagem em português; cancelamento com motivo curto recusado e com motivo válido aceito.
- **Como auditor:** vê o rascunho e os itens, sem nenhum botão de alteração.
- **Queda do servidor:** ao salvar um item com o backend desligado, a tela avisa e mantém o que foi digitado; salvar de novo, com o backend ligado, criou um único item.
- **Prova:** 14 quebras de propósito em `receiving.ts`, `format.ts` e no nome do produto foram pegas pelos testes. Antes disso, uma quebra no dígito verificador (resto 0 ou 1) passou despercebida, porque o teste repetia o cálculo; ele passou a usar chaves fixas geradas pela regra do backend, e duas das 14 confirmam esse ponto.

### Verificado no servidor real (2026-11-04)
- **Celular do dono do projeto:** informado por ele como "Funcionou tudo", para a lista pedida: aba Recebimento visível; recebimento com rejeição e falta; finalização com o resultado correto. Resultado relatado, com CSP de produção e PostgreSQL.
- **CI:** execução 41803250512 aprovada, com as corridas em PostgreSQL.

### NÃO verificado
- Edição e remoção de item, cancelamento, recusa de nota repetida e visão do auditor no servidor real: foram percorridas só no ambiente local.
- As telas não têm teste automatizado de interface; só as regras em `lib/` têm. O percurso acima foi manual.

### Limitações e riscos
- **O recebimento exige conexão.** Ao contrário da contagem, não há fila no aparelho: sem rede, a tela avisa e nada é salvo.
- `FinalizeIn.note` existe na API, mas o serviço não grava. Por isso a tela não oferece esse campo.
- Lote e validade não podem ser editados pela API: é preciso remover o item e lançar de novo, e a tela avisa.

## Fuso da loja e validade à noite

**Defeito corrigido:** a validade era comparada com o dia em UTC. No Brasil, à noite, produto que vencia naquele dia era recusado como vencido. Agora o dia do recebimento é o dia no fuso da loja (`stores.timezone`, migração `0008`).

- Lista fechada com os 4 fusos do Brasil. Lojas existentes e novas começam em **Brasília (UTC−3)**, valor assumido: ajustar em Configuração se a loja estiver em outro fuso. Só administrador altera, com auditoria.
- `received_at` com fuso passa a ser gravado em UTC, e `GET /receivings/{id}` devolve `received_on`, que a tela usa para conferir a validade. O fuso do aparelho não influi.
- Mudar o fuso vale para as próximas conferências, inclusive em rascunhos abertos; itens já gravados não são recalculados. Nova dependência: `tzdata`, porque a imagem do Docker e o Windows podem não trazer a base de fusos.

### Verificado por execução (Windows, SQLite)
- `ruff check .` limpo; `pytest -q` com 216 aprovados e 16 corridas ignoradas (sem PostgreSQL local); `alembic upgrade head`, `check`, `downgrade -1` e `upgrade head` sem pendência; `npm test` com 39 aprovados; `npm run build` limpo.
- **Testes:** 23h30 em Brasília (já outro dia em UTC) aceita o produto que vence no dia e recusa o vencido na véspera; o mesmo instante dá resultados diferentes em Noronha e no Acre; a edição de item segue a mesma regra; permissões, loja de outra organização (404), fuso inválido (422) e auditoria.
- **Prova:** 6 quebras de propósito foram pegas, inclusive a volta do defeito original.
- **No navegador:** a troca do fuso em Configuração continua valendo depois de recarregar a página.

### NÃO verificado
- Conferência real à noite em celular e no servidor; a migração e a base `tzdata` na imagem de produção, até a próxima implantação.
- **CI:** o envio de `3c9272f` não gerou execução, durante uma instabilidade do GitHub. O commit seguinte, só de documentação, serve para disparar a verificação do mesmo código.

## Domínio de produção

`gestor.sysordemsistemas.com.br`, informado pelo dono do projeto. **Implantado em 2026-11-04 no servidor provisório, com ERP simulado**, pelo dono do projeto, que executou os comandos de `docs/IMPLANTACAO.md`. O servidor não foi acessado por esta sessão.

### Verificado no servidor real (2026-11-04)
- **Teste de fumaça:** informado pelo dono do projeto como "Tudo certo", depois de corrigir o e-mail do comando. A primeira execução, vista em captura de tela, falhou só nas 7 verificações que dependem do login, porque usou o texto de exemplo no lugar do e-mail.
- **Consultado de fora, sem login:** HTTP redireciona para HTTPS; certificado Let's Encrypt válido até 2027-02-02; CSP, HSTS e demais cabeçalhos presentes; API responde 401 sem login; `central.sysordemsistemas.com.br` responde 200.
- **Navegador do painel:** tela de login sem erros no console e service worker ativo sob a CSP, com o cache `mp-shell-v2` criado.
- **Celular do dono do projeto:** informado por ele como "Funcionou tudo", para a lista pedida: abrir sem aviso; entrar, criar loja e local, sincronizar o catálogo e contar; contagem em modo avião enviada ao voltar a internet; instalar o app. Resultado relatado, sem registro de aparelho ou navegador.

### Entregue
`infra/deploy/` (compose de produção, verificação somente leitura do servidor, implantação, backup, teste de fumaça, instalação do site) e `infra/nginx/` (site e cabeçalhos de segurança). Começa com ERP simulado.

### Verificado por execução, no job `ensaio-implantacao` do CI (execução 41738519497)
- Implantação completa com nginx, Docker e PostgreSQL, com um site vizinho no mesmo nginx: o arquivo dele não mudou e ele continuou no ar.
- Interface, link direto, cabeçalhos, cache, documentação da API fechada, banco sem porta publicada, backend só em `127.0.0.1`, primeiro admin, login, cookie `Secure`, renovação.
- Limite de login atrás do proxy: IP falsificado não escapa, outro IP real não é bloqueado.
- Segunda implantação preserva os dados e faz backup; o backup restaura em um banco separado; instalar o site de novo é recusado.
- **Prova:** com o IP real sem chegar ao backend (execução 41739288734) e com o cliente podendo falsificar o IP (execução 41739304666), o ensaio falhou nas verificações certas.
- No navegador, com os mesmos cabeçalhos: login, menu, logos e Configuração sem violação de CSP.

### NÃO verificado
- **Roteiro completo com a equipe:** só o dono do projeto testou, em um celular. Vários usuários contando ao mesmo tempo, outros aparelhos e o restante de `docs/ROTEIRO_TESTE_CELULAR.md` não foram relatados.
- **Renovação do certificado:** o `certbot.timer` está ativo, mas a renovação não foi ensaiada.
- **Backup agendado:** o `crontab` descrito em `docs/IMPLANTACAO.md` não foi confirmado; sem ele, o backup só roda em nova implantação sobre banco já migrado.
- **Revisão com a CSP:** login, Configuração e contagem foram usados com a CSP ativa; a tela de revisão não foi citada no teste.

### Riscos
- O servidor é **provisório, para protótipos**, com 1 CPU compartilhada: não usar com dados reais.
- O redirecionamento em HTTP, criado pelo certbot, revela a versão do nginx (`Server: nginx/1.28.3`). O site em HTTPS não revela. Baixa gravidade.
- `requirements.txt` usa versões mínimas (`>=`): implantações em datas diferentes podem instalar versões diferentes. Fixar antes de usar dados reais.
- Backups ficam no mesmo servidor até alguém copiá-los para fora. Trocar `POSTGRES_PASSWORD` depois da instalação exige alterar também dentro do banco.
- Sem monitoramento nem alerta de queda.

O servidor também **atende outras aplicações**: nunca rodar `docker system prune` nem `docker volume prune` nele.

## Teste em celular real: PARCIAL
O dono do projeto testou em um celular pelo domínio publicado (ver "Domínio de produção"). Falta o roteiro completo com a equipe. O registro abaixo é do modo pela rede local, anterior à implantação.

`npm run celular` serve o frontend por HTTPS autoassinado na rede local (porta 5174); o backend continua só em `127.0.0.1`, atrás do proxy. Roteiro em `docs/ROTEIRO_TESTE_CELULAR.md`.

- **Verificado neste computador:** página responde 200 por HTTPS em `192.168.1.10:5174`; a API passa pelo proxy (401 sem login); login de teste em `localhost` responde 200 com cookie `HttpOnly`; o backend não responde pelo IP da rede.
- **Não testável neste modo:** service worker, abrir sem internet e instalar o app exigem certificado válido. Por isso, são testados em `https://gestor.sysordemsistemas.com.br`.

## Próximo passo
1. Fazer o roteiro completo com a equipe, no domínio publicado, corrigindo o que aparecer.
2. Com token de **homologação**, validar as leituras e os pontos abertos de `docs/SYSORDEM_CONTRATOS.md`.
3. Etapa 5: Central de Movimentações, ainda sem escrita no ERP real.
