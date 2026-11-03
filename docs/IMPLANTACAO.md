# Implantação em `gestor.sysordemsistemas.com.br`

Passo a passo para quem tem acesso ao servidor. **Nunca envie senha, chave SSH ou o conteúdo de `.env.producao` por chat, e-mail ou Git.**

O servidor é compartilhado com `central.sysordemsistemas.com.br`. Os scripts só criam arquivos do próprio MercadoPilot: um site novo no nginx, uma pasta em `/var/www/gestor`,
e contêineres, rede e volume com o nome `mercadopilot`. Esse caminho foi ensaiado no CI, com um site vizinho que continuou igual e no ar; ensaio não é o servidor real.

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
sudo -E ./teste_fumaca.sh email-do-admin@exemplo.com.br
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

## Antes de usar dados reais
O ERP real exige homologação e autorização (`docs/SYSORDEM_CONTRATOS.md`). Trocar para `ERP_ADAPTER=sysordem` também exige `SECRETS_ENCRYPTION_KEY` no `.env.producao`; o token é cadastrado na tela de Configuração.
