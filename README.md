# 🎮 MaratoNando

Bot de Discord para **maratonas de jogos com roleta**. Cada pessoa coloca seus jogos na roleta, o bot sorteia, e o jogo que cair é o que todo mundo joga.

**Regra de ouro: caiu, jogou. Sem reclamar.** (E quem reclamar vai pro Mural da Vergonha. 😤)

## O que ele faz

- **Painel da maratona** com botões: adicionar jogos, ver os seus, sortear e encerrar.
- **Jogos secretos**: ninguém vê os jogos dos outros até eles serem sorteados.
- **Roleta animada**: a roleta passa pelos participantes, para em alguém e revela o jogo.
- **Sem repetição**: jogos repetidos são recusados (sem diferenciar maiúsculas) e cada jogo sai uma vez só.
- **Limite opcional** de jogos por pessoa.
- **Lista pessoal**: lembrou de um jogo no meio da semana? `/lista adicionar` guarda ele. Na maratona, ao clicar em **Adicionar jogos**, a lista aparece para você marcar o que quer colocar na roleta. Jogos sorteados saem da lista sozinhos, e os que não saíram voltam para ela quando a maratona acaba.
- **📡 Radar de jogos**: o bot acompanha streamers escolhidos pelo servidor (ex.: alanzoka) na Twitch, vê o que eles jogam e cruza com a Steam. Só vira sugestão o jogo que é **multiplayer**, **barato** (até R$ 60, configurável) e **bem avaliado** (70%+). Dá para salvar as sugestões direto na lista pessoal, e o bot pode avisar num canal quando aparecer jogo novo.
- **🛒 Sugestões da Steam** (`/sugestao`): jogos co-op online em alta, novidades ou em promoção, filtrados pelas tags da Steam para sobrar só friendslop: precisa ter tag de jogar junto (Online Co-Op, Party Game…) **e** de diversão (Funny, Comedy, Social Deduction, Physics…), nenhuma tag de jogo "sério" (MMO, Competitive, Sports), preço baixo e 80%+ de avaliações positivas.
- **👥 Sorteio de times** (`/times`): começa a lista com quem está na call (ou com os participantes da maratona), pergunta se mais alguém vai jogar e só então sorteia times equilibrados.
- **🧹 Chat limpo**: com `/config canal-do-bot`, o bot ganha um canal próprio onde as mensagens ficam. Em qualquer outro canal, as mensagens dele somem sozinhas depois de alguns minutos (padrão: 10). Se alguém estiver usando os botões de uma mensagem, o prazo recomeça; e o painel de uma maratona aberta só some depois que ela acaba.
- **💸 Avisos de promoção**: a cada 6 horas o bot confere na Steam o preço dos jogos que estão nas listas pessoais. Quando um deles entra em promoção (ou o desconto aumenta), ele avisa no canal do bot marcando (com notificação) só quem tem aquele jogo na lista. O `/lista ver` também mostra quais jogos da sua lista estão em promoção.
- **Mural da Vergonha**: `/reclamou @pessoa`, ou botão direito na pessoa → Apps → **Reclamou!**
- **Resumo final**: o que foi jogado, em que ordem, quem escolheu, o que ficou na roleta e o ranking de reclamações.
- **Aguenta reinícios**: tudo fica salvo em SQLite, e os botões do painel continuam funcionando depois que o bot reinicia.
- Funciona em **vários servidores** ao mesmo tempo (uma maratona aberta por servidor).

## Comandos

| Comando | O que faz | Quem pode |
|---|---|---|
| `/maratona criar [nome] [limite]` | Abre uma maratona no canal e posta o painel | Qualquer pessoa |
| `/maratona sortear` | Gira a roleta (o mesmo que o botão 🎲) | Quem organizou ou quem tem *Gerenciar servidor* |
| `/maratona painel` | Traz o painel para o fim do chat | Qualquer pessoa |
| `/maratona encerrar` | Encerra e mostra o resumo (pede confirmação) | Quem organizou ou quem tem *Gerenciar servidor* |
| `/maratona ajuda` | Explica como funciona | Qualquer pessoa |
| `/lista adicionar [jogo]` | Guarda um jogo na sua lista pessoal (sem o nome, abre um formulário para vários) | Qualquer pessoa (só ela vê) |
| `/lista ver` | Mostra sua lista e deixa apagar jogos dela | Qualquer pessoa (só ela vê) |
| `/reclamou @pessoa` | Manda alguém pro Mural da Vergonha | Qualquer pessoa |
| `/sugestao [tipo]` | Sugestões de friendslop só pela Steam: em alta, novidades ou em promoção | Qualquer pessoa |
| `/times [quantidade]` | Monta a lista de quem vai jogar e sorteia os times (2 a 8) | Qualquer pessoa (só quem pediu sorteia) |
| `/config canal-do-bot [canal]` | Define o canal do bot; fora dele, as mensagens do bot somem sozinhas (vazio = desliga) | Quem tem *Gerenciar servidor* |
| `/config apagar-depois minutos` | Quanto tempo as mensagens ficam fora do canal do bot (0 = nunca apagar) | Quem tem *Gerenciar servidor* |
| `/config promocoes ativo` | Liga/desliga os avisos de promoção (vão para o canal do bot) | Quem tem *Gerenciar servidor* |
| `/config ver` | Mostra as configurações | Quem tem *Gerenciar servidor* |
| `/radar ver` | Mostra as sugestões de jogos, com menu para salvar na sua lista | Qualquer pessoa |
| `/radar streamers` | Mostra quem o radar acompanha, o preço máximo e o canal de alertas | Qualquer pessoa |
| `/radar adicionar-streamer canal` | Passa a acompanhar um canal da Twitch (nome ou link) | Quem tem *Gerenciar servidor* |
| `/radar remover-streamer canal` | Para de acompanhar um canal | Quem tem *Gerenciar servidor* |
| `/radar alertas [canal]` | Avisa nesse canal quando surgir jogo novo no radar (vazio = desliga) | Quem tem *Gerenciar servidor* |
| `/radar preco-maximo [reais]` | Muda o preço máximo das sugestões (vazio = R$ 60) | Quem tem *Gerenciar servidor* |

## Como rodar no seu PC (Windows)

### 1. Criar o bot no Discord

1. Entre em <https://discord.com/developers/applications> e clique em **New Application**. Dê o nome *MaratoNando*.
2. No menu da esquerda, vá em **Bot** e clique em **Reset Token**. Copie o token que aparecer (ele só aparece uma vez). **Nunca mostre esse token a ninguém**: com ele, qualquer pessoa controla o bot.
3. Ainda em **Bot**, nenhuma das "Privileged Gateway Intents" precisa ser ligada.
4. No menu da esquerda, vá em **OAuth2 → URL Generator**:
   - Em *Scopes*, marque `bot` e `applications.commands`.
   - Em *Bot Permissions*, marque `View Channels`, `Send Messages` e `Embed Links`.
   - Copie a URL gerada no fim da página, abra no navegador e escolha o servidor.

### 2. Preparar a pasta

Abra o terminal (PowerShell) dentro da pasta do bot e rode:

```powershell
python -m venv .venv            # cria um ambiente isolado só para este projeto
.venv\Scripts\activate          # ativa o ambiente (o terminal passa a mostrar "(.venv)")
pip install -r requirements.txt # instala discord.py e python-dotenv
```

### 3. Configurar o token

1. Faça uma cópia do arquivo `.env.example` e renomeie a cópia para `.env`.
2. Abra o `.env` e cole o token depois de `DISCORD_TOKEN=`.
3. **Para testar**, preencha também `DEV_GUILD_ID` com o ID do seu servidor (no Discord: Configurações → Avançado → ligue o *Modo desenvolvedor*; depois clique com o botão direito no ícone do servidor → **Copiar ID do servidor**). Assim os comandos aparecem na hora, em vez de demorar alguns minutos.

### 4. (Opcional) Ligar o radar: chaves da Twitch

O radar usa a API oficial da Twitch, que é gratuita mas pede um cadastro. Sem essas chaves o bot funciona normalmente, só o `/radar` fica desligado.

1. Sua conta da Twitch precisa ter a **verificação em duas etapas** ligada (Twitch → Configurações → Segurança e privacidade).
2. Entre em <https://dev.twitch.tv/console> e clique em **Register Your Application** (Registrar seu aplicativo).
3. Preencha:
   - **Name**: qualquer nome único, ex.: `MaratoNando-SeuNome`.
   - **OAuth Redirect URLs**: `http://localhost` (o bot não usa, mas o campo é obrigatório).
   - **Category**: `Chat Bot` (ou `Other`).
   - **Client Type**: `Confidential`.
4. Clique em **Create** e depois em **Manage** no aplicativo criado.
5. Copie o **Client ID** e cole no `.env` depois de `TWITCH_CLIENT_ID=`.
6. Clique em **New Secret**, copie o segredo e cole depois de `TWITCH_CLIENT_SECRET=`. Assim como o token do Discord, **não mostre esse segredo a ninguém**.

Depois de ligar o bot, use `/radar adicionar-streamer alanzoka` no servidor. O bot olha os clipes do último mês para descobrir o que ele andou jogando, confere cada jogo na Steam, e daí em diante checa a cada 10 minutos o que ele está jogando ao vivo.

### 5. Ligar

```powershell
python bot.py
```

Quando aparecer `Logado como MaratoNando...`, está funcionando. Para desligar, aperte `Ctrl + C` no terminal.

## Colocando na Discloud

O arquivo `discloud.config` já está pronto. Na hora de subir:

1. Deixe `DEV_GUILD_ID` **vazio** no `.env` (assim os comandos ficam disponíveis em todos os servidores).
2. Compacte em um `.zip` os arquivos `bot.py`, `db.py`, `radar.py`, `sugestao.py`, `times.py`, as pastas `cogs` e `servicos`, `requirements.txt`, `discloud.config` e o `.env`.
3. Suba o `.zip` na Discloud.

> Se você testou com `DEV_GUILD_ID` e depois ligou sem ele, os comandos podem aparecer **duplicados** no servidor de testes. Para resolver, ligue o bot uma vez localmente com `DEV_GUILD_ID` preenchido e comente a linha `self.tree.copy_global_to(guild=guild)` no `bot.py`: isso limpa os comandos do servidor de testes.

## Testes

As regras da maratona (sorteio, repetidos, limite, lista, mural, reinício) e do radar (filtro, ranking, leitura das respostas da Steam) têm testes automáticos que rodam sem precisar do Discord nem de internet:

```powershell
pip install -r requirements-dev.txt
python -m pytest
```

## Estrutura

```
MaratoNando/
├── bot.py             # liga o bot, carrega os comandos e registra no Discord
├── db.py              # banco de dados SQLite e todas as regras da maratona
├── radar.py           # regras do radar: o que é "friendslop" e como ranquear
├── sugestao.py        # leitura da busca da Steam e filtro de friendslop por tags
├── times.py           # divisão equilibrada em times
├── cogs/
│   ├── maratona.py    # comandos, botões, formulários, roleta e embeds da maratona
│   ├── radar.py       # /radar, /sugestao e a coleta automática a cada 10 minutos
│   ├── times.py       # /times
│   ├── limpeza.py     # /config e a faxina das mensagens fora do canal do bot
│   └── promocoes.py   # vigia de promoções dos jogos das listas
├── servicos/
│   ├── twitch.py      # cliente da API oficial da Twitch
│   └── steam.py       # consultas à loja da Steam
├── tests/
│   ├── test_db.py     # testes da maratona e da lista
│   ├── test_radar.py  # testes do radar (com respostas reais da Steam)
│   ├── test_sugestao.py # testes do filtro do /sugestao
│   ├── test_times.py  # testes do sorteio de times
│   ├── test_limpeza.py # testes da agenda de limpeza
│   └── test_promocoes.py # testes dos avisos de promoção
├── requirements.txt
├── .env.example       # modelo do arquivo de configuração
└── discloud.config
```

A separação é proposital: `db.py` e `radar.py` não sabem nada de Discord nem de internet (só recebem e devolvem dados), então as regras podem ser testadas sozinhas. Os `cogs/` cuidam da conversa com o Discord, e os `servicos/` da conversa com a Twitch e a Steam.

## Tecnologias

Python 3.10+ · [discord.py](https://discordpy.readthedocs.io/) 2.x (comandos de barra, botões persistentes, modais, menus de seleção e de contexto) · SQLite · aiohttp (API Helix da Twitch e loja da Steam) · tarefas em segundo plano (`discord.ext.tasks`) · pytest
