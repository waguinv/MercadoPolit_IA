# Roteiro de teste no celular

Teste pela rede local, sem deploy: o computador serve o sistema e o celular abre pelo Wi-Fi. Só há **dados fictícios e ERP simulado**.
E-mails e senha de teste estão em [`scripts/dev_local.py`](../scripts/dev_local.py).

## Preparar
No computador, a partir da raiz do repositório, em dois terminais:
```bash
backend/.venv/Scripts/python.exe scripts/dev_local.py
```
```bash
cd frontend && npm run celular
```
1. Descubra o IP do computador com `ipconfig` (ex.: `192.168.1.10`).
2. Conecte o celular ao **mesmo roteador**. Abra `https://IP-DO-COMPUTADOR:5174`.
3. O navegador avisará que a conexão não é particular: o certificado é autoassinado. Em "Avançado", escolha continuar.

**Se não abrir:** o firewall do Windows costuma bloquear redes marcadas como "Pública". Permita o Node.js quando o Windows perguntar, ou marque a rede
do computador como "Privada". Rede de visitantes também costuma isolar os aparelhos.

**Ao terminar:** pare o `npm run celular`, pois ele deixa o sistema acessível para a rede local.

## O que este modo não testa
O certificado é autoassinado, então **o navegador recusa o service worker e a instalação do app**. Abrir sem internet e o ícone na tela inicial
precisam de HTTPS com certificado válido, ou seja, de um ambiente publicado. A fila de contagens funciona, desde que a página não seja recarregada sem conexão.

## Roteiro
Anote o modelo do celular, o navegador e o que estranhar. Peça para cada pessoa fazer sozinha, sem ajuda: onde ela travar é o que mais interessa.

### Como gerente
1. Entre e crie uma sessão no local **Salão da loja**.
2. A tela ficou legível? Algum botão ficou pequeno ou escondido pelo teclado ou pelo menu inferior?

### Como operador (de preferência em outro celular)
3. Entre e abra a sessão.
4. Busque um produto por nome. O teclado atrapalhou?
5. Digite a quantidade com vírgula em um produto em quilos, como **Banana Prata**, e salve. Quantas toques foram necessários?
6. Conte 10 produtos em sequência. O ritmo é aceitável para um corredor inteiro?
7. Tente uma fração em um produto em unidades. O aviso ficou claro?
8. **Sem rede:** ligue o modo avião, conte um produto e veja o aviso. Desligue o modo avião e toque em "Enviar agora", ou aguarde até 30 segundos. A contagem foi enviada uma vez só?
9. Bloqueie a tela por alguns minutos e volte. Continuou logado, no mesmo lugar?
10. Conte o mesmo produto do gerente, com outra quantidade.

### Como gerente de novo
11. Em Revisar, abra o item divergente, consulte o ERP e aceite uma contagem com justificativa.
12. Peça uma reconferência de outro item. Ela apareceu para o operador?
13. Termine a contagem e encerre a sessão.

### No uso real
14. Luz fraca, luvas e uma mão só: dá para usar?
15. A falta de leitura de código de barras pela câmera pesa? Hoje ela não existe.
16. Depois de 30 minutos de uso, a bateria e o login se comportaram bem?

## Resultado
Para cada problema, anote: o passo, o que aconteceu, o que esperava e, se possível, uma foto da tela. Traga isso para a próxima etapa.
