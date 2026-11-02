# Instruções de uso — Claude Code e MercadoPilot AI

## 1. Preparar
Coloque estes arquivos em `docs/` no repositório:
- `MERCADOPILOT_AI_ARQUITETURA.md`
- `PROMPT_MESTRE_CLAUDE_CODE_MERCADOPILOT_AI.txt`
- `INSTRUCOES_USO_CLAUDE_CODE_MERCADOPILOT_AI.md`

Faça backup ou commit antes de começar. Abra a pasta correta do repositório e confira `git status` e `git branch --show-current`. Não inclua credenciais SysOrdem nos prompts nem no Git.

## 2. Primeiro prompt para Claude Code
> Leia integralmente os três documentos em `docs/`. Inspecione repositório, branch, estado do Git, stack e testes antes de modificar arquivos. Preserve o trabalho existente. Apresente o estado atual, riscos e um plano pequeno para a primeira etapa. Aguarde minha aprovação antes de alterações que impactem dados, dependências importantes, integração real, infraestrutura ou produção.

Depois aprove uma etapa específica. Evite solicitar o sistema inteiro de uma só vez.

## 3. Ciclo de cada etapa
1. Definir escopo e critérios de aceitação.
2. Implementar somente o escopo aprovado.
3. Revisar `git diff` e arquivos alterados.
4. Executar testes, lint e verificações de tipos disponíveis.
5. Confirmar que `.env`, tokens e senhas não foram adicionados ao Git.
6. Fazer commit depois de revisar.
7. Atualizar README e documentação.
8. Só então avançar.

Comandos úteis: `git status`, `git diff --stat`, `git diff`, `git branch --show-current`.
Não execute comandos destrutivos sem compreender o efeito.

## 4. Configuração
Use `.env.example` com valores fictícios. Esses valores são exemplos, não credenciais. Adicione `.env` ao `.gitignore`. Use variáveis protegidas ou gerenciador de segredos para homologação.

## 5. Integração SysOrdem
- Comece com mocks; não conecte produção na fase inicial.
- Confirme contrato, permissões e efeito de cada endpoint no Swagger e em homologação.
- Um endpoint que define saldo final não é um movimento universal.
- Timeout/resposta ambígua deve gerar estado de integração desconhecida; reconciliar antes de retry.
- Toda escrita precisa de idempotência, auditoria e autorização.
- Não desative validação de estoque automaticamente na produção.
- Listagem de XML processado não prova suporte à importação de NF-e de entrada.

## 6. Critérios mínimos de aceite
- Aplicação inicia com instruções documentadas.
- Migrações funcionam em banco de desenvolvimento limpo.
- Testes relevantes passam e resultados são informados honestamente.
- Autorização no back-end e isolamento entre organizações.
- Erros e estados de integração são visíveis e auditáveis.
- Nenhum segredo versionado.
- Nenhuma escrita no ERP real, deploy ou alteração DNS sem autorização explícita.

## 7. Continuidade entre sessões
Manter `docs/STATUS_DESENVOLVIMENTO.md` com etapa concluída, arquivos alterados, decisões, comandos/resultados reais, pendências, riscos e próxima etapa.

Prompt para retomar:
> Leia a arquitetura, o prompt mestre e `docs/STATUS_DESENVOLVIMENTO.md`. Verifique o estado real do repositório e do Git. Não presuma que itens planejados foram executados. Resuma o ponto atual e proponha a próxima etapa antes de modificar arquivos.

## 8. Limite desta documentação
Estes documentos definem requisitos e plano. Não significam que o software já foi implementado, testado, integrado ao SysOrdem ou publicado. Só declare resultados após execução e verificação reais.
