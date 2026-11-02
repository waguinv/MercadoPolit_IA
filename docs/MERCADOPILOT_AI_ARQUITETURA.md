# MercadoPilot AI — Arquitetura e Especificação Funcional

**Versão:** 1.0 | **Idioma:** português do Brasil

## 1. Visão
MercadoPilot AI é uma plataforma web/PWA mobile-first para operações, controle de estoque e inteligência gerencial de supermercados. É multi-tenant e integra-se ao SysOrdem ERP por um adaptador isolado. O ERP continua sendo a fonte oficial de vendas, compras, dados fiscais e saldo de estoque quando houver API adequada. A IA começa como consultiva: não executa ajustes, compras ou aprovações automaticamente.

## 2. Princípios obrigatórios
- Isolamento de dados por organização e loja; escopo aplicado no servidor.
- Adaptador único para o SysOrdem; não espalhar chamadas HTTP pelos módulos.
- Idempotência e auditoria em toda escrita externa.
- Timeout ou resposta ambígua não é falha confirmada: consultar/reconciliar antes de repetir.
- Nunca somar contagens conflitantes automaticamente.
- Nunca usar ajuste de saldo absoluto como movimento universal.
- Nunca contornar validações de estoque automaticamente.
- Segredos fora do Git e dos logs.
- Entrega incremental, testável e documentada.
- Não fazer deploy, alterar DNS ou escrever no ERP real sem autorização explícita.

## 3. Stack proposta
- Front-end: React + TypeScript + Vite, responsivo e PWA.
- Back-end: Python + FastAPI, API REST `/api/v1`.
- Persistência: PostgreSQL, SQLAlchemy e Alembic.
- Tarefas: Redis + Celery ou alternativa justificada.
- Testes: Pytest, Playwright.
- Ambiente local: Docker Compose; produção com proxy reverso e HTTPS.
- CI: GitHub Actions para lint, tipos e testes.

## 4. Estrutura sugerida
```text
mercadopilot-ai/
  frontend/src/{app,components,features,lib}/
  backend/app/{api/v1,core,db,models,schemas,repositories,services,integrations/sysordem,workers}/
  backend/tests/
  docs/
  infra/nginx/
  scripts/
  docker-compose.yml
  .env.example
  .gitignore
  README.md
```

## 5. Multi-tenancy e permissões
Toda entidade de negócio deve estar vinculada a `organization_id` e, quando necessário, `store_id`. O servidor deriva a organização da sessão autenticada; não confia em identificador enviado pelo cliente. Todas as leituras, escritas, exportações e tarefas assíncronas respeitam o tenant. Papéis iniciais: administrador, gerente/responsável, operador e consulta/auditoria. Permissões devem ser verificadas no back-end. Quando a segregação estiver ativa, quem registra consumo não pode aprová-lo.

## 6. Integração SysOrdem — fatos e limites
- Foi identificado um endpoint de ajuste de estoque que recebe `qtdSaldo` como saldo final desejado. Isso **não** o torna apropriado para toda saída ou movimento.
- Há `POST /api/documento/v1` com modelos `PA`, `PV`, `OC` e `CN`, mas significados e efeitos exatos no estoque precisam ser confirmados em homologação.
- Há endpoint para listar XMLs processados; isso não prova que exista importação de NF-e de entrada por XML.
- Há `POST /api/mercadoria/{id}/fichas-tecnicas/producao/v1`, com dados de usuário, quantidade, local, lote/validade, cálculo de perda e validação de estoque. A resposta pode gerar documentos de entrada, saída e perda.
- Estoque insuficiente pode gerar conflito. Não usar `validarEstoque=false` automaticamente; exceções exigem política, autorização e auditoria.
- Revalidar contratos contra o Swagger disponível e em homologação. Não presumir que endpoint está habilitado para a conta.

Antes de escrita real, confirmar em homologação: tipos de documento e efeitos, perdas, devoluções, consumo interno, transferências, importação de XML de entrada, idempotência, atomicidade/concorrência, releitura do saldo e permissões. Enquanto não confirmado, usar mocks e não escrever no ERP real.

## 7. Módulos

### 7.1 Cadastros
Organizações, lojas, locais, usuários, papéis, permissões, departamentos, finalidades e centros de custo. Produtos/unidades devem priorizar a fonte ERP e evitar cadastros duplicados sem estratégia de sincronização.

### 7.2 Inventário colaborativo
Sessões por loja/local/setor; atribuição opcional; contagens simultâneas, salvamento imediato e retomada. Registrar cada contagem com usuário e horário. Divergência, unidade incompatível, duplicidade ou movimento posterior à contagem exige reconferência/revisão. Antes de corrigir, reler o saldo ERP e reconciliar movimentos posteriores. Aplicar ajuste idempotente somente quando seguro e reler o saldo depois. Se o ERP só aceitar saldo absoluto sem controle atômico/condicional, encaminhar conflito para revisão manual.

### 7.3 Recebimento
Recebimento manual sem pedido obrigatório. Entrada de XML apenas se confirmada; XML preenche dados esperados, mas nunca atualiza estoque automaticamente. Registrar fornecedor, data, local, documento, produto/SKU/código de barras, unidade, quantidades recebida/aceita/rejeitada/pendente, avarias, falta, excesso, item incorreto, lote e validade. Permitir rascunho e parcial. Finalizar após conferência e confirmação autorizada. Falha/timeout de integração permanece pendente de reconciliação. Deduplicar NF-e pela chave de acesso quando aplicável e cada operação por chave idempotente. Preservar XML original com acesso restrito e auditoria.

### 7.4 Central de Movimentações Operacionais
Ponto único com formulários contextuais, campos compartilhados e histórico unificado. Campos comuns: organização/loja, produto, SKU/código de barras, quantidade/unidade, local, data/hora, registrador, justificativa, observações, anexos, estado, chave idempotente e referência externa.

**Perdas/avarias:** embalagem danificada, deterioração, vencimento, quebra/acidente, contaminação, perda operacional e outros. Registrar quantidade, local, lote/validade quando disponíveis, responsável, evidência e aprovação conforme política. Perda é saída classificada, não ajuste de inventário.

**Devolução/troca com fornecedor:** fornecedor, produto, quantidade, motivo, recebimento/NF de origem, evidências, modalidade (troca, crédito ou devolução sem reposição), coleta e reposição esperada. Saída devolvida e reposição são eventos distintos. Reposição só entra após recebimento e conferência. Não inventar documentos fiscais nem duplicar movimento já realizado no ERP.

**Produção da padaria:** usar ficha técnica/receita para consumir ingredientes e registrar produto acabado/semielaborado. Validar estoque, registrar responsável, quantidade, local, lote/validade e perdas. Preferir ficha técnica do ERP e verificar se o ERP já faz as baixas/entradas para evitar duplicidade. Estoque insuficiente exige bloqueio ou exceção autorizada.

**Consumo interno:** departamentos configuráveis: refeitório, escritório/administrativo, operacional, frutaria, padaria, açougue, limpeza e outros. Finalidades: almoço de funcionários, café/lanche, uso administrativo, uso na operação, limpeza/higienização e outros. Preparação que gera produto acabado deve usar produção/ficha técnica, não consumo genérico. Modo configurável por organização/loja/departamento: autoatendimento do funcionário ou registro centralizado pelo responsável. Todo consumo aguarda aprovação de gerente/responsável antes de movimentar estoque. Registrar consumidor, registrador e aprovador separadamente; rejeição não movimenta estoque; permitir solicitação de correção; bloquear autoaprovação se a segregação estiver ativa. Estados: `draft`, `pending_approval`, `approved`, `rejected`, `needs_correction`, `integration_pending`, `integration_error`, `integration_unknown`, `integrated`, `cancelled`. Cancelamento pós-integração exige movimento compensatório autorizado, nunca exclusão. Relatórios por departamento, finalidade, produto, loja e período; custo apenas se a fonte for confiável.

**Transferências:** saída na origem e entrada no destino vinculadas, somente após confirmar a representação no ERP.

### 7.5 Painel e IA
Começar com indicadores determinísticos e dados auditáveis. Preparar alertas de estoque, divergências, perdas, consumo e recebimentos pendentes. IA consultiva resume padrões e sugere ações para revisão humana; deve exibir período, fonte e limitações, sem inventar causa ou números e sem escrita automática.

## 8. Modelo de dados conceitual
`Organization`, `Store`, `StockLocation`, `User`, `Membership`, `Role`, `Permission`, `ProductReference`, `UnitReference`, `InventorySession`, `InventoryCount`, `InventoryDiscrepancy`, `InventoryRecount`, `Receiving`, `ReceivingItem`, `ReceivingDocument`, `OperationalMovement`, `MovementApproval`, `InternalConsumption`, `InternalConsumptionApproval`, `IntegrationJob`, `IntegrationAttempt`, `ExternalReference`, `AuditEvent`, `Attachment`, `Department`, `Purpose`, `CostCenter`. Todo registro de negócio precisa de tenant, timestamps e auditoria apropriada. Usar `Decimal` para valores monetários; quantidades devem ter unidade explícita.

## 9. Rotas internas exemplificativas
`POST /api/v1/inventory-sessions`; `POST /api/v1/inventory-sessions/{id}/counts`; `POST /api/v1/inventory-sessions/{id}/recounts`; `POST /api/v1/receivings`; `POST /api/v1/receivings/{id}/finalize`; `POST /api/v1/operational-movements`; `POST /api/v1/internal-consumptions`; `POST /api/v1/internal-consumptions/{id}/approve`; `/reject`; `/request-correction`; `GET /api/v1/reports/consumption`; `GET /api/v1/integrations/{id}/status`. São exemplos, não contrato fechado.

## 10. Segurança e testes
Autenticação segura, autorização no servidor, isolamento entre tenants, proteção de anexos, validação de uploads, rate limiting, logs sem segredos, auditoria append-only e retenção conforme política. Testar serviços, transições, tenant, permissões, idempotência, timeout/retry, concorrência, fluxos de recebimento/perda/produção/consumo e interface mobile/desktop. Testes comuns nunca escrevem no ERP real.

## 11. Fases
1. Fundação: repositório, Docker, configuração, CI, banco, migrações, autenticação e tenant.
2. Cadastros e leitura ERP via adaptador/mocks.
3. Inventário colaborativo.
4. Recebimento.
5. Central de movimentações.
6. Produção via ficha técnica após homologação.
7. Relatórios.
8. IA consultiva.
9. Segurança, backup/restauração, observabilidade e preparação para produção.

## 12. Pendências antes de produção
Confirmar efeitos dos documentos `PA`, `PV`, `OC`, `CN`; APIs para perdas/devoluções/consumo/transferências; importação de XML de entrada; idempotência e concorrência; mapeamento de unidades/locais/produtos/custos; credenciais e ambiente de homologação. Até confirmação, desenvolver localmente com adaptador simulado.
