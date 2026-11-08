# Implantação em `gestor.sysordemsistemas.com.br`

Passo a passo para quem tem acesso ao servidor. **Nunca envie senha, chave SSH ou o conteúdo de `.env.producao` por chat, e-mail ou Git.**

O servidor é compartilhado com `central.sysordemsistemas.com.br`. Os scripts só criam arquivos do próprio MercadoPilot: um site novo no nginx, uma pasta em `/var/www/gestor`,
e contêineres, rede e volume com o nome `mercadopilot`. Esse caminho foi ensaiado no CI, com um site vizinho que continuou igual e no ar; ensaio não é o servidor real.

## Servidor atual: provisório, em uso com dados reais por decisão do dono
Verificado em 2026-11-03: Ubuntu 26.04, **1 CPU**, 3,8 GB de memória (2,7 GB disponíveis), 10 GB livres em disco, Docker, nginx e certbot instalados, porta 8011 livre. Ele atende outros sistemas.
- **Decisão de 2026-11-05:** o dono do projeto, também dono do Cristal Supermercado, decidiu entrar em produção e corrigir o que aparecer na operação. Passos em "Colocar o Cristal em produção".
- **Riscos aceitos nessa decisão:** CPU compartilhada e nenhum alerta de queda. Backup só protege contra a perda da máquina depois de copiado para fora dela.
- **Implante em horário de pouco uso:** a compilação pode ocupar a única CPU por alguns minutos e deixar os outros sistemas lentos.
- O MercadoPilot usa no máximo 1 GB de memória (limite de 512 MB em cada contêiner) e recusa implantar com menos de 4 GB livres em disco.
- **Nunca rode `docker system prune` nem `docker volume prune` aqui**: atingem os outros sistemas.

## Como fica
```text
Internet → nginx do servidor (HTTPS) ─┬─ interface: arquivos em /var/www/gestor
                                      └─ /api/ → 127.0.0.1:8011 → contêiner do backend → contêiner do PostgreSQL
```
O banco não abre porta, e o backend só escuta em `127.0.0.1`.

## 1. Conferir o servidor (não altera nada)
```bash
curl -fsSL https://raw.githubusercontent.com/waguinv/MercadoPolit_IA/main/infra/deploy/verificar_servidor.sh | sudo bash
```
O relatório não mostra senhas. **Pare e revise** se aparecer: Docker ou Compose não instalado, porta 8011 ocupada, "ATENÇÃO" em alguma linha, ou pouca memória (recomendado: 1 GB livre).
Se a porta estiver ocupada, é preciso trocar `BACKEND_PORT` e também `infra/nginx/gestor.conf`.

## 2. Baixar o código e criar os segredos
```bash
sudo git clone https://github.com/waguinv/MercadoPolit_IA.git /opt/mercadopilot
cd /opt/mercadopilot/infra/deploy
sudo cp .env.example .env.producao
sudo chmod 600 .env.producao
sudo sed -i "s/^SECRET_KEY=.*/SECRET_KEY=$(openssl rand -hex 32)/; s/^POSTGRES_PASSWORD=.*/POSTGRES_PASSWORD=$(openssl rand -hex 16)/" .env.producao
```
O ambiente começa com o **ERP simulado** (`ERP_ADAPTER=mock`), com dados fictícios e sem acesso ao ERP real.
Se o repositório virar privado, o `git clone` passa a exigir uma chave de implantação.

## 3. Subir o sistema
```bash
sudo ./implantar.sh
sudo ./instalar_nginx.sh
```

## 4. Criar o primeiro administrador
```bash
sudo ./compose.sh exec backend python /scripts/create_admin.py
```
A senha é pedida na tela, sem aparecer. Use no mínimo 12 caracteres.

## 5. Conferir antes do HTTPS
`teste_fumaca.sh` cria tentativas de login erradas em uma conta inexistente, de propósito, e usa o IP local do servidor.
```bash
read -rs SMOKE_PASSWORD && export SMOKE_PASSWORD
sudo env SMOKE_PASSWORD="$SMOKE_PASSWORD" ./teste_fumaca.sh email-do-admin@exemplo.com.br
```

## 6. Ativar o HTTPS
```bash
sudo certbot --nginx -d gestor.sysordemsistemas.com.br
```
Escolha redirecionar HTTP para HTTPS. Depois disso, **não rode `instalar_nginx.sh` de novo**: ele recusa, porque sobrescreveria o certificado na configuração.

## 7. Conferir no navegador do celular
O ensaio do CI não tem navegador, então estes pontos só podem ser vistos aqui:
- [ ] `https://gestor.sysordemsistemas.com.br` abre sem aviso de segurança.
- [ ] `https://central.sysordemsistemas.com.br` continua normal.
- [ ] Entrar, criar loja e local, sincronizar o catálogo e fazer uma contagem.
- [ ] **Modo offline:** com o app já aberto uma vez, ligue o modo avião e recarregue. Precisa abrir. Se não abrir, é o ponto não verificado do service worker junto com a política de segurança (CSP).
- [ ] Instalar na tela inicial e ver o ícone.
- [ ] Fechar tudo, abrir de novo e continuar logado.

## 8. Agendar o backup
```bash
sudo crontab -e
# 0 3 * * * /opt/mercadopilot/infra/deploy/backup.sh >> /var/log/mercadopilot-backup.log 2>&1
```
Os backups ficam em `/var/backups/mercadopilot` por 14 dias. **Ainda no mesmo servidor:** copie para fora, senão não protegem contra a perda da máquina.
Guarde também o `.env.producao` em local seguro, fora do servidor.

## Atualizar
```bash
cd /opt/mercadopilot && sudo git pull --ff-only && sudo infra/deploy/implantar.sh
```
Faz backup, roda as migrações, troca o backend e só depois a interface. Há uma breve indisponibilidade enquanto o backend reinicia.

## Atualização automática
Ligada por decisão do dono do projeto (2026-11-07). A cada 10 minutos, o servidor confere o `main` do GitHub e roda `implantar.sh` quando:
- há commit novo que só avança o que o servidor tem;
- **todas** as execuções do CI desse commit terminaram aprovadas.

CI pendente: espera a próxima rodada. CI reprovado: não implanta. Falha na implantação: não repete sozinho e fica no log. Nenhuma senha ou chave é guardada no GitHub: é o servidor que busca.

Ligar, uma vez, depois de atualizar o código:
```bash
cd /opt/mercadopilot && sudo git pull --ff-only && sudo infra/deploy/instalar_atualizacao_automatica.sh
```
Acompanhar e desligar:
```bash
sudo journalctl -u mercadopilot-atualizar -n 50
sudo systemctl list-timers mercadopilot-atualizar.timer
sudo /opt/mercadopilot/infra/deploy/instalar_atualizacao_automatica.sh --remover
```
**Efeitos:** uma versão aprovada no CI entra em produção em até cerca de 10 minutos depois de o CI terminar, em qualquer horário. Durante a implantação, a CPU fica ocupada por alguns minutos e o backend fica fora do ar por alguns segundos. Cada implantação faz um backup antes de migrar; acompanhe o espaço em disco.

## Redefinir a senha de uma pessoa
```bash
cd /opt/mercadopilot && sudo git pull --ff-only
sudo infra/deploy/compose.sh exec backend python /scripts/redefinir_senha.py
```
A senha é pedida duas vezes e não aparece ao digitar. Depois de muitas tentativas erradas, o login pode continuar bloqueado até acabar o tempo indicado na tela.

## Voltar a uma versão anterior
```bash
sudo git checkout <commit-anterior> && sudo infra/deploy/implantar.sh
```
Isso **não desfaz migrações**. Se a versão nova mudou o banco, restaure o backup feito antes dela, sabendo que o que foi registrado depois se perde:
```bash
sudo ./compose.sh stop backend
gzip -dc /var/backups/mercadopilot/ARQUIVO.sql.gz | sudo ./compose.sh exec -T db psql -q -v ON_ERROR_STOP=1 -U mercadopilot -d mercadopilot
sudo ./compose.sh start backend
```

## Consultar e remover
```bash
sudo ./compose.sh ps
sudo ./compose.sh logs --tail 100 backend
```
**`docker compose down -v` apaga o banco.** Não use `-v` em produção.

## Colocar o Cristal em produção
Para o servidor onde o sistema já está instalado. A plataforma só **lê** o SysOrdem; nada é escrito nele. Rode em horário de pouco uso.

1. Atualize o código e ligue o ERP real. `ativar_erp_real.sh` não mostra a chave e nunca troca uma chave existente.
```bash
cd /opt/mercadopilot && sudo git pull --ff-only
sudo infra/deploy/ativar_erp_real.sh
sudo infra/deploy/implantar.sh
```
2. Crie a organização da empresa. Use um e-mail diferente do administrador de teste. A organização de teste continua no banco, mas sem ERP.
```bash
sudo infra/deploy/compose.sh exec backend python /scripts/create_admin.py
```
3. Entre em `https://gestor.sysordemsistemas.com.br` com esse administrador. Em **Configuração**, cole o token e clique em **Testar e conectar**.
4. Clique em **Sincronizar catálogo** e aguarde. No computador, 14.886 produtos terminaram dentro do limite de 10 minutos; se aparecer erro de tempo, pare e registre o horário.
5. Crie a loja e o local, escolhendo o local do ERP na lista. Depois, crie os usuários da equipe.
6. Copie para fora do servidor o `.env.producao` e os backups, e agende o backup (seção 8). Sem a chave que está no `.env.producao`, os tokens salvos ficam ilegíveis e precisam ser cadastrados de novo.
7. Para desfazer a leitura do ERP real: troque `ERP_ADAPTER=real` por `mock` no `.env.producao` e rode `implantar.sh`. Isso não apaga dados.

Escrever no SysOrdem continua proibido sem homologação (`docs/SYSORDEM_CONTRATOS.md`). Mais detalhes em `docs/INTEGRACOES.md`.
