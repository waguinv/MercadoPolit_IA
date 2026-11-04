# Integrações por API

Cada organização conecta o próprio sistema na tela **Configuração**: cola o token, e a plataforma testa antes de salvar. A plataforma **só lê** o sistema conectado; nada é escrito nele.

Hoje há um conector: **SysOrdem**.

## Testar no computador, sem mexer no servidor
Use este caminho primeiro. Os dados ficam só na sua máquina, em `backend/dev_real.db`, e o token fica cifrado nesse arquivo.

Em dois terminais, a partir da raiz do repositório:
```bash
backend/.venv/Scripts/python.exe scripts/dev_local.py --erp-real
```
```bash
cd frontend && npm run dev
```
1. Abra http://localhost:5173 e entre com `admin@exemplo.com.br`. A senha de teste está em `scripts/dev_local.py`.
2. Em **Configuração**, cole o token e clique em **Testar e conectar**.
3. Confira se Unidades e Locais de estoque aparecem com as quantidades esperadas.
4. Clique em **Sincronizar catálogo**. Catálogos grandes demoram, por causa do limite de 20 requisições por minuto do SysOrdem.
5. Crie uma loja e um local, e escolha o local do ERP na lista.

Para recomeçar do zero, pare o backend e apague `backend/dev_real.db`.

## Ligar no servidor
Exige autorização do dono do projeto e dados reais só depois de decidir sobre o servidor provisório.

1. Gere a chave: `python scripts/generate_secrets_key.py`. Guarde uma cópia fora do servidor: sem ela, será preciso cadastrar os tokens de novo.
2. Em `.env.producao`, defina `ERP_ADAPTER=real` e `SECRETS_ENCRYPTION_KEY=<a chave>`.
3. Implante a versão nova, conforme `docs/IMPLANTACAO.md`.
4. Crie a organização da empresa: `sudo ./compose.sh exec backend python /scripts/create_admin.py`.
5. Entre com esse administrador e siga os passos 2 a 5 da seção anterior.

Com `ERP_ADAPTER=real`, uma organização sem conexão fica sem ERP: o catálogo já copiado continua visível, mas o que precisa ler o ERP responde que a integração está indisponível.

## Regras que a plataforma aplica
- **Token recusado não é salvo** e não substitui o token que já funcionava.
- **Dados não se misturam:** uma organização com catálogo de uma origem (simulado ou outro sistema) recusa outra. Por isso, a empresa real entra em uma **organização nova**.
- **Trocar para o token de outra empresa não é detectado.** Conferir a empresa antes de substituir o token é responsabilidade de quem administra: a próxima sincronização desativaria os produtos que não existem na outra empresa.
- O token nunca volta pela API nem aparece em auditoria.
- A URL de cada sistema vem do ambiente do servidor, nunca da tela.

## Adicionar outro sistema
1. Crie `backend/app/integrations/<sistema>/adapter.py` com os métodos de `ErpAdapter` (`erp_port.py`). Só leitura, com as falhas traduzidas para `ErpError`, e devolvendo a lista completa ou erro: a sincronização desativa o que não vier.
2. Se o sistema não trabalhar com token único por empresa, é preciso estender `ErpConnectionIn` e a cifragem.
3. Acrescente uma entrada em `CONNECTORS`, em `backend/app/integrations/registry.py`, e a URL em `core/config.py`.
4. Escreva testes como os de `tests/test_sysordem_adapter.py`, com transporte HTTP simulado.
5. Homologue as leituras com um token real antes de usar em produção.

A tela passa a mostrar a escolha do sistema assim que houver mais de um conector. Não há conector genérico configurado pela tela: cada sistema tem formatos e regras próprios, e uma URL digitada abriria risco de SSRF.
