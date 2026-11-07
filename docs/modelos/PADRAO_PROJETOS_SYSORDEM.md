# Padrão dos projetos SysOrdem: instruções para o Claude Code

Este arquivo vale para **todos os projetos do universo SysOrdem** do dono (waguinv).
Como usar em um projeto novo: copie este arquivo para `docs/PADRAO_PROJETOS_SYSORDEM.md` e acrescente ao `CLAUDE.md` do projeto a linha
`@docs/PADRAO_PROJETOS_SYSORDEM.md`. O `CLAUDE.md` do projeto pode detalhar ou restringir estas regras; nunca afrouxá-las.

Implementação de referência: repositório `waguinv/MercadoPolit_IA` (MercadoPilot AI). Antes de criar algo destas seções em um
projeto novo, leia como foi feito lá e adapte, em vez de reinventar.

## 1. Idioma e tom
- Responda, documente, escreva mensagens de commit e todos os textos de tela em **português do Brasil**.
- O dono não precisa ler código: explique o que muda para quem usa, com passos numerados e comandos em blocos separados.
- Comandos para o servidor: deixe claro que rodam **no terminal do servidor** (Linux), não no PowerShell do Windows.

## 2. Ritmo de trabalho
- Quando o dono disser **"continue"** ou **"próximo passo"**, siga para a próxima etapa pendente do documento de status do projeto,
  sem pedir aprovação de plano.
- Ao fim de cada etapa: testes verdes, lint limpo, migrações conferidas (subir, conferir, descer e subir de novo), documento de status
  atualizado separando **verificado** de **NÃO verificado**, e então **commit e push no `main` sem pedir confirmação**.
- Nunca force push. Se o remoto divergir, pare e pergunte.
- **Nunca declare teste, integração ou implantação que não rodou.** Diga o que ficou sem verificar.
- Toda regra crítica nova: quebre de propósito, confirme que algum teste falha e restaure.

## 3. Publicar é colocar em produção
Nos projetos com atualização automática, **push no `main` com CI aprovado entra no servidor sozinho em até ~10 minutos**.
- Só publique o que foi testado. Migração que apaga ou transforma dados exige autorização do dono **antes** do push.
- Títulos de commit claros, em português, pensados para o dono ler: eles aparecem no aviso de versão do app.

### Como a atualização automática deve funcionar (padrão)
- O **servidor busca** a atualização (agendamento do systemd a cada 10 minutos). Nenhuma senha ou chave do servidor fica no GitHub.
- Implanta só quando: há commit novo no `main`, o avanço é fast-forward e **todas** as execuções do CI desse commit terminaram aprovadas.
- Compara o GitHub com a **versão que está no ar** (o `commit` do `versao.json` publicado), nunca com o código baixado:
  um `git pull` feito à mão baixa o código sem implantar.
- Uma versão que falhou ao implantar não é tentada de novo sozinha (cada tentativa faz backup e compila).
- O script roda dentro de uma função (`main "$@"; exit`), porque o `git merge` troca o próprio arquivo durante a execução.
- Referência: `infra/deploy/atualizar_automatico.sh`, `instalar_atualizacao_automatica.sh` e `implantar.sh` no MercadoPilot.
  Ensaie os cenários (sem mudança, CI pendente, reprovado, aprovado, `git pull` à mão, falha, divergência) antes de publicar.

## 4. Avisar o dono de cada atualização
Enquanto o projeto estiver em desenvolvimento:
- **No app:** a implantação publica `/versao.json` com `versao` (commit curto), `commit`, `data` e `mudancas` (títulos dos commits desde
  a versão anterior no ar). O app mostra "App atualizado para a versão X" com a lista, até a pessoa clicar em **Entendi**, e, com o app
  aberto, confere a cada 5 minutos e ao voltar para a tela: se houver versão mais nova, mostra **Atualizar agora**.
  Referência: `frontend/src/lib/versao.ts` e `frontend/src/components/AvisoVersao.tsx` no MercadoPilot.
- **No chat:** depois de cada push, acompanhe o CI e diga ao dono a versão e o que mudou. Confirme que entrou no ar lendo o
  `/versao.json` do domínio do projeto. Se não entrar, diga e oriente o diagnóstico; nunca presuma.

## 5. Suporte: código de tela e Central SysOrdem
- Toda tela tem um **código fixo** no padrão `APP-MOD-NN`: sigla da aplicação (3 letras, definida pelo dono), módulo (3 letras) e número
  (2 dígitos). Exemplo: `MPI-INV-02`. Códigos nunca mudam nem são reaproveitados.
- O catálogo de telas fica num único arquivo, com teste que acusa rota sem código e código repetido.
- Selo no rodapé de toda tela logada, com o código e a versão; o clique copia o texto para o suporte: aplicação, versão, código da tela,
  rota modelo (nunca o id do registro). **Nunca** senha, token ou dado pessoal.
- Erro inesperado: tela de erro com o código da tela e um código curto do erro; o log registra só códigos e o tipo do erro.
- O suporte de todas as aplicações converge para a **Central SysOrdem** (`waguinv/SysOrdem-Central`). Siga o documento
  "Padrão de suporte SysOrdem" e a ordem de etapas dele; não crie um canal de suporte paralelo.
- Referência: `frontend/src/lib/telas.ts` e `frontend/src/components/Suporte.tsx` no MercadoPilot.

## 6. Segurança
- **Nunca** peça, mostre, grave ou registre senhas, tokens ou chaves no chat, no Git ou em logs. Se o dono mostrar uma senha num print,
  avise que ela ficou exposta e não a use.
- Segredos só em arquivos ignorados pelo Git (ex.: `.env.producao`, `chmod 600`) ou cifrados no banco.
- Tenant e permissões aplicados no servidor. Integrações com sistemas de terceiros isoladas num adaptador e, até homologação,
  **somente leitura**.
- Ainda exigem autorização explícita: escrever em sistema de terceiros (ERP), mudar o servidor fora da atualização automática, DNS,
  trocar a stack, apagar dados.

## 7. Servidor compartilhado
Os sistemas SysOrdem dividem servidores (ex.: o de `gestor`, `central`, `app`, `dashbord`).
- Cada projeto usa só os próprios nomes: contêineres, rede e volume com o nome do projeto, site próprio no nginx, porta própria.
- **Nunca** rode `docker system prune`, `docker volume prune` nem `docker compose down -v` em produção.
- Implantação faz backup antes de migrar; lembre o dono de copiar backups e o arquivo de segredos para fora do servidor.

## 8. Telas
- Identidade visual SysOrdem: use os tokens de cor do projeto `sysordem-dashbord`; não invente cores.
- Nada decorativo: todo botão funciona. Toda tela tem saída (Voltar ou menu) e, quando falta um pré-requisito, mostra o caminho
  até ele (ex.: "Ir para Configuração"), não só "peça a um administrador" para quem já é administrador.
- Pensado para celular primeiro; confira no navegador em tamanho de celular antes de declarar pronto.

## 9. Ao iniciar um projeto novo, pergunte ao dono
1. Nome e sigla de 3 letras da aplicação (para os códigos de tela).
2. Domínio e servidor onde vai rodar.
3. Se a atualização automática e o aviso de versão devem ser ligados desde o início (padrão: sim).
