# SysOrdem — contratos confirmados no Swagger

**Fonte:** `swagger.json` enviado pelo cliente (OpenAPI 3.0.1, 286 operações, servidor
`https://api.meuerponline.com.br/publica`). **Swagger não é homologação:** o que está aqui prova que o
endpoint *existe e como é descrito*, não que esteja habilitado para a conta nem que se comporte como a
descrição sugere. Nada de escrita foi chamado, simulado ou implementado.

## 1. Autenticação e limites
| Item | Confirmado no Swagger | Consequência no projeto |
|---|---|---|
| Autenticação | Header `Authorization: Authentication {token}`; empresa identificada **pelo token** | Um token por organização, guardado **cifrado** (`erp_connections`). `SYSORDEM_CLIENT_ID/SECRET` do `.env.example` original não existem nesta API e foram removidos |
| Rate limit | Documentado em `info.description`: **mesmo limite por token** em todos os endpoints; balde inicial de **20 requisições**, reposição de **20 a cada 1 minuto**, **fila de 10**; excedido → `429` | Adaptador espera e repete **apenas leituras**. Um sync com N páginas leva ~N/20 min depois do balde inicial → virar tarefa em segundo plano (Celery); evitar chamadas por item em lote |
| `Retry-After` | Não documentado | Honrado se vier; senão backoff crescente |
| Paginação | `page` (padrão 1), `limit` (padrão 50), resposta com `hasNext`, `total`, `totalPages` | Adaptador segue `hasNext`; o **máximo de `limit` não é documentado** → `SYSORDEM_PAGE_SIZE` configurável (padrão 100), validar em homologação |
| Idempotência | **Nenhum endpoint aceita chave de idempotência** | Toda escrita futura precisa de idempotência *nossa* + reconciliação por leitura antes de qualquer retry |
| Quantidades | `number:double` (ponto flutuante) | Convertidas com `Decimal(str(valor))`; nunca somar floats |
| Fuso horário | `date-time` sem fuso explícito | `last_changed_at` é devolvido sem fuso afirmado |

## 2. Leituras já implementadas (adaptador `SysordemErpAdapter`)
| Operação da porta | Endpoint | Mapeamento |
|---|---|---|
| `check_connection` | `GET /api/empresa/v1` | Qualquer 200 = token válido; nenhum dado da empresa é guardado/exposto |
| `list_units` | `GET /api/embalagem/v1` | `sigla`→código, `descricao`→nome, `fracionado=="S"`→permite fração (ignora `ativo=="N"`) |
| `list_products` | `GET /api/mercadoria/v1` | **ID externo = `codigoMercadoriaVariacao`** (é a chave de estoque); `ativo=="S"`; SKU = `referenciaVariacao` ou `referenciaMercadoria`; unidade = `embalagem` |
| `list_stock_locations` | `GET /api/local-estoque/v1` | `codigo`→código (guardado em `StockLocation.external_code`) |
| `get_stock_balance` | `GET /api/mercadoria/{variacao}/local-estoque/{local}/estoque/v1` | `qtdSaldo`, `qtdSaldoReserva`, `dataHora`; **local é obrigatório**; saldo ausente/inválido = erro, **nunca zero** |

Ressalvas: a lista de mercadorias traz status da mercadoria e da variação consolidado em `ativo`; produto
sem `embalagem` (ou com embalagem inativa) é **ignorado e reportado** no sync (`products_skipped`), não
inventamos unidade. Custos (`valCusto`/`valCustoMedio`) existem no ERP, mas **não** são importados.

## 3. Descobertas que afetam o desenho (Etapa 3 em diante)
1. **O ERP tem módulo de inventário nativo**: `POST /api/inventario/v1` (cria por local; "não permitido
   quando já existe inventário aberto para o local"), `POST /api/inventario/{id}/mercadorias/v1` (lança itens
   com `idUsuario`), `PUT .../mercadorias/v1/{idSequencia}` (edita item), `POST /api/inventario/{id}/encerrar/v1`
   (encerra), mais listagens (`GET /api/inventario/v1` com `dataInicio/dataFim/status`, itens e encerramentos).
   É o caminho natural para **aplicar** contagens, em vez do `PUT .../estoque/v1` de saldo absoluto.
2. O encerramento registra `qtdContada` e `qtdAnterior` (saldo "anterior considerado no encerramento"):
   o ERP provavelmente grava a contagem como saldo final **no momento do encerramento**. Vendas/recebimentos
   entre a contagem e o encerramento podem ser sobrescritos → **a conciliação com movimentos posteriores é
   responsabilidade da plataforma** (reler saldo, comparar com o saldo de referência, enviar divergência para
   revisão humana). A listagem de encerramentos permite verificar `qtdAnterior` depois (a confirmar).
3. `PUT .../estoque/v1` (saldo absoluto) "gera documento para manter o histórico", mas não tem versão/condição
   → **não é movimento universal** nem seguro sob concorrência. Uso só com revisão manual e política explícita.
4. Reconciliação após timeout é viável por leitura: inventário (listar por data/status/local), itens e
   documentos podem ser consultados antes de repetir um `POST`. A confirmar em homologação como identificar
   *nosso* registro (não há campo de referência externa garantido; a `descricao`/`observacao` é uma opção).
5. **Não existem** no Swagger endpoints dedicados a: perdas/avarias, devoluções/trocas com fornecedor, consumo
   interno, transferências, nem importação de NF-e de entrada por XML. `POST /api/documento/v1` aceita só os
   modelos `PA`, `PV`, `OC`, `CN`, **sem descrição de efeito no estoque**. Esses módulos seguem em mock até haver
   confirmação com o fornecedor do ERP.
6. `POST /api/mercadoria/{id}/fichas-tecnicas/producao/v1` existe (produção da padaria) — exige homologação
   (Etapa 6), conforme a arquitetura.

## 4. Endpoints PROIBIDOS no projeto
- **`POST /api/consulta/sql/v1`**: executa SQL arbitrário no banco da organização. Nunca usar (nem para leitura,
  nem "só para teste"); um token com esse acesso é uma credencial de altíssimo risco.
- Qualquer escrita sem autorização explícita do responsável, homologação e política de idempotência.
- `validarEstoque=false` (ficha técnica) de forma automática.
- Escritas fiscais/financeiras (`/emitir`, `/inutilizar`, `/pagar`, `/receber`, certificado etc.) estão
  **fora do escopo** do produto.

## 5. Perguntas a confirmar em homologação (antes de qualquer escrita real)
1. Limite real de req/min por token e tamanho máximo de `limit`; o 429 traz `Retry-After`?
2. Fuso horário de `dataHora`; precisão de `qtdSaldo` (casas decimais).
3. O inventário nativo faz o ERP **definir o saldo = contado** no encerramento? E o que ocorre com movimentos
   posteriores à abertura/contagem? Existe bloqueio de movimentação durante inventário aberto?
4. Como localizar com segurança um inventário/documento nosso após timeout (campo de referência, descrição)?
5. Efeito exato de `PA`, `PV`, `OC`, `CN` no estoque; existe forma de registrar perda/consumo/devolução?
6. Permissões do token: o usuário de consulta/integração tem acesso aos endpoints de escrita? `idUsuario`
   precisa mapear usuários da plataforma ↔ usuários do ERP (`/api/usuario/validar/v1` valida credenciais do
   ERP — **não** coletar senhas de usuários do ERP na plataforma).
7. Existe ambiente de homologação separado, com token próprio?

## 6. Riscos operacionais
- Token vazado dá acesso à empresa inteira no ERP: guardar só cifrado, rotacionar, nunca logar (testado).
- A URL base vem só do ambiente (anti-SSRF). Conexões de organizações diferentes nunca compartilham token.
- Perder `SECRETS_ENCRYPTION_KEY` invalida os tokens cifrados (reconfigurar); fazer backup da chave à parte.
