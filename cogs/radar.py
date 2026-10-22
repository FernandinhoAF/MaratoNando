"""Radar de jogos: sugere jogos "friendslop" cruzando o que streamers jogam na Twitch com a Steam.

Como funciona:
  • O servidor escolhe streamers para acompanhar (/radar adicionar-streamer alanzoka).
  • Ao adicionar, o bot olha os clipes do último mês para saber o que ele andou jogando.
  • A cada 10 minutos, o bot vê quem está ao vivo e qual jogo está na tela.
  • Cada jogo novo é procurado na Steam (preço, se é multiplayer, avaliações).
  • O botão 📡 Radar dos streamers do /o-que-jogar mostra os que passam no filtro; se tiver canal de alertas, o bot avisa os novos.
"""

import asyncio
import logging
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

from cogs.maratona import juntar, plural, texto_resultado, tratar_erro
from marca import COR_DETALHE
from db import Database, ErroMaratona, agora
from radar import JANELA_DIAS, PRECO_MAX_PADRAO, InfoJogo, Sugestao, eh_jogo, montar_info, ranquear
from servicos.steam import PAUSA, ErroSteam, SteamClient
from sugestao import APROVACAO_MINIMA, NOMES_TIPOS, TIPOS, ItemLoja, filtrar
from servicos.twitch import ErroAPI, TwitchClient

log = logging.getLogger("maratonando.radar")

COR_RADAR = COR_DETALHE  # ciano (Manual da Marca)
INTERVALO_MINUTOS = 10
LOGIN_TWITCH = re.compile(r"^[a-z0-9_]{3,25}$")
CACHE_LOJA_SEGUNDOS = 6 * 3600  # a lista da Steam é guardada por 6 horas
POR_PAGINA = 6
# Buscas feitas para montar as sugestões da loja: as 3 primeiras páginas de co-op online
# e mais uma página para cada tag de diversão (Funny, Party Game, Social Deduction).
BUSCAS_LOJA = ({"start": 0}, {"start": 100}, {"start": 200},
               {"tags": 4136}, {"tags": 7178}, {"tags": 745697})


# ---------------------------------------------------------------- formatação

def numero_curto(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} mi".replace(".", ",")
    if n >= 1_000:
        return f"{n // 1_000} mil"
    return str(n)


def linha_steam(info: InfoJogo) -> str:
    if info.gratis:
        preco = "🆓 Grátis"
    elif info.preco_texto:
        preco = f"💰 {info.preco_texto}" + (f" (−{info.desconto}%)" if info.desconto else "")
    else:
        preco = "💰 sem preço no Brasil"
    if info.review_total:
        avaliacao = f"👍 {round((info.review_pct or 0) * 100)}% de {numero_curto(info.review_total)} avaliações"
    else:
        avaliacao = "👍 sem avaliações ainda"
    modo = "🤝 Co-op" if info.coop else "⚔️ Multiplayer"
    return f"{preco} · {avaliacao} · {modo}"


def linha_streamers(s: Sugestao, nomes: dict[str, str]) -> str:
    quem = ", ".join(f"{nomes.get(login, login)} ({plural(d, 'dia', 'dias')})"
                     for login, d in sorted(s.dias_por_streamer.items(), key=lambda x: -x[1]))
    dia = date.fromisoformat(s.ultimo_dia)
    return f"🎥 {quem} · última vez em {dia.day:02d}/{dia.month:02d}"


def ler_login(texto: str) -> str:
    """Aceita "alanzoka", "@alanzoka" ou "https://twitch.tv/alanzoka"."""
    texto = texto.strip().lower().rstrip("/")
    texto = re.sub(r"^(https?://)?(www\.|m\.)?twitch\.tv/", "", texto)
    return texto.lstrip("@")


# ---------------------------------------------------------------- componentes

class SalvarSugestaoBotao(discord.ui.DynamicItem[discord.ui.Button], template=r"radar:salvar:(?P<game_id>\d+)"):
    """Botão "Salvar na minha lista" dos alertas.

    O ID do jogo vai dentro do custom_id do botão, então ele continua
    funcionando mesmo depois que o bot reinicia.
    """

    def __init__(self, game_id: str) -> None:
        super().__init__(discord.ui.Button(label="Salvar na minha lista", emoji="💾",
                                           style=discord.ButtonStyle.primary, custom_id=f"radar:salvar:{game_id}"))
        self.game_id = game_id

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(match["game_id"])

    async def callback(self, interaction: discord.Interaction) -> None:
        db: Database = interaction.client.db  # type: ignore[attr-defined]
        info = db.radar_jogos([self.game_id]).get(self.game_id)
        if info is None:
            await interaction.response.send_message("Não achei mais esse jogo no radar. 🤔", ephemeral=True)
            return
        salvos, recusados = db.salvar_na_lista(interaction.user.id, [info.nome_exibicao])
        if info.appid:
            db.salvar_steam_nome(info.nome_exibicao, info.appid)  # já sei qual jogo é: poupa uma busca
        await interaction.response.send_message(texto_resultado(salvos, recusados, "Salvo na sua lista"),
                                                ephemeral=True)


class SugestoesView(discord.ui.View):
    """Menu embaixo do radar para cada pessoa salvar sugestões na própria lista."""

    def __init__(self, db: Database, sugestoes: list[Sugestao]) -> None:
        super().__init__(timeout=900)
        self.db = db
        self.nomes = {s.info.game_id: s.info.nome_exibicao for s in sugestoes}
        self.appids = {s.info.game_id: s.info.appid for s in sugestoes}
        menu = discord.ui.Select(
            placeholder="💾 Salvar jogos na minha lista…", min_values=1, max_values=len(sugestoes),
            options=[discord.SelectOption(label=s.info.nome_exibicao[:100], value=s.info.game_id)
                     for s in sugestoes],
        )
        menu.callback = self.salvar
        self.menu = menu
        self.add_item(menu)

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    async def salvar(self, interaction: discord.Interaction) -> None:
        nomes = [self.nomes[v] for v in self.menu.values]
        salvos, recusados = self.db.salvar_na_lista(interaction.user.id, nomes)
        for v in self.menu.values:
            if self.appids.get(v):
                self.db.salvar_steam_nome(self.nomes[v], self.appids[v])
        await interaction.response.send_message(texto_resultado(salvos, recusados, "Salvo na sua lista"),
                                                ephemeral=True)


class SugestoesLojaView(discord.ui.View):
    """Páginas das sugestões da loja (botões do /o-que-jogar): menu para salvar na lista + botão para ver mais."""

    def __init__(self, cog: "RadarCog", itens: list[ItemLoja], tipo: str, preco_max: int) -> None:
        super().__init__(timeout=900)
        self.cog = cog
        self.itens = itens
        self.tipo = tipo
        self.preco_max = preco_max
        self.pagina = 0
        self.montar()

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    @property
    def paginas(self) -> int:
        return max(1, -(-len(self.itens) // POR_PAGINA))  # divisão arredondando para cima

    def da_pagina(self) -> list[ItemLoja]:
        inicio = self.pagina * POR_PAGINA
        return self.itens[inicio:inicio + POR_PAGINA]

    def montar(self) -> None:
        self.clear_items()
        itens = self.da_pagina()
        menu = discord.ui.Select(
            placeholder="💾 Salvar jogos na minha lista…", min_values=1, max_values=len(itens),
            options=[discord.SelectOption(label=i.nome[:100], value=str(i.appid)) for i in itens],
        )
        menu.callback = self.salvar
        self.add_item(menu)
        if self.paginas > 1:
            botao = discord.ui.Button(label="Mais sugestões", emoji="🔄", style=discord.ButtonStyle.secondary)
            botao.callback = self.proxima
            self.add_item(botao)

    def embed(self) -> discord.Embed:
        preco = f"R$ {self.preco_max / 100:.2f}".replace(".", ",")
        e = discord.Embed(
            title=f"🛒 Sugestões da Steam · {NOMES_TIPOS[self.tipo]}", color=COR_RADAR,
            description=(f"Jogos **co-op online** com cara de friendslop (tags como Funny, Party Game, "
                         f"Social Deduction), até **{preco}** e com **{round(APROVACAO_MINIMA * 100)}%+** "
                         "de avaliações positivas."),
        )
        for n, item in enumerate(self.da_pagina(), start=self.pagina * POR_PAGINA + 1):
            if item.gratis:
                preco_item = "🆓 Grátis"
            else:
                preco_item = f"💰 {item.preco_texto or '—'}" + (f" (−{item.desconto}%)" if item.desconto else "")
            avaliacao = f"👍 {round((item.review_pct or 0) * 100)}% de {numero_curto(item.review_total)} avaliações"
            tags = ", ".join(item.tags_divertidas[:3])
            e.add_field(name=f"{n}. {item.nome}",
                        value=f"{preco_item} · {avaliacao}\n🏷️ {tags} · [Ver na Steam]({item.url})", inline=False)
        if self.da_pagina() and self.da_pagina()[0].imagem:
            e.set_thumbnail(url=self.da_pagina()[0].imagem)
        e.set_footer(text=f"Página {self.pagina + 1} de {self.paginas} · 📡 Radar dos streamers (no /o-que-jogar) mostra o que eles jogam")
        return e

    async def salvar(self, interaction: discord.Interaction) -> None:
        menu: discord.ui.Select = self.children[0]  # type: ignore[assignment]
        escolhidos = {int(v) for v in menu.values}
        nomes = [i.nome for i in self.da_pagina() if i.appid in escolhidos]
        salvos, recusados = self.cog.db.salvar_na_lista(interaction.user.id, nomes)
        for i in self.da_pagina():
            if i.appid in escolhidos:
                self.cog.db.salvar_steam_nome(i.nome, i.appid)
        await interaction.response.send_message(texto_resultado(salvos, recusados, "Salvo na sua lista"),
                                                ephemeral=True)

    async def proxima(self, interaction: discord.Interaction) -> None:
        self.pagina = (self.pagina + 1) % self.paginas
        self.montar()
        await interaction.response.edit_message(embed=self.embed(), view=self)


# ---------------------------------------------------------------- cog

class RadarCog(commands.Cog):
    # Configurações do radar: só aparecem para quem tem "Gerenciar servidor".
    # Ver as sugestões do radar é pelo botão "📡 Radar dos streamers" do /o-que-jogar.
    radar = app_commands.Group(
        name="radar",
        description="Configura o radar: quais streamers acompanhar, preço máximo e canal de alertas.",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]
        self.session: Optional[aiohttp.ClientSession] = None
        self.twitch: Optional[TwitchClient] = None
        self.steam: Optional[SteamClient] = None
        self.verificando = asyncio.Lock()
        self.tarefas: set[asyncio.Task] = set()
        self.cache_loja: dict[str, tuple[float, list[ItemLoja]]] = {}
        self.buscando_loja = asyncio.Lock()

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(SalvarSugestaoBotao)
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.steam = SteamClient(self.session)
        client_id, secret = os.getenv("TWITCH_CLIENT_ID"), os.getenv("TWITCH_CLIENT_SECRET")
        if client_id and secret:
            self.twitch = TwitchClient(self.session, client_id, secret)
            self.ciclo.start()
        else:
            log.warning("TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET não configurados: o radar fica desligado.")

    async def cog_unload(self) -> None:
        self.ciclo.cancel()
        if self.session:
            await self.session.close()

    async def cog_app_command_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    # ---- coleta em segundo plano -----------------------------------

    @tasks.loop(minutes=INTERVALO_MINUTOS)
    async def ciclo(self) -> None:
        try:
            await self.olhar_ao_vivo()
            await self.verificar_steam()
            await self.enviar_alertas()
        except Exception:  # um erro num ciclo não pode derrubar os próximos
            log.exception("Erro no ciclo do radar")

    @ciclo.before_loop
    async def antes_do_ciclo(self) -> None:
        await self.bot.wait_until_ready()

    async def olhar_ao_vivo(self) -> None:
        logins = self.db.radar_todos_logins()
        if not logins or self.twitch is None:
            return
        for stream in await self.twitch.ao_vivo(logins):
            game_id, nome = stream.get("game_id"), stream.get("game_name") or ""
            if game_id and eh_jogo(nome):
                self.db.radar_registrar_visto(stream["user_login"], game_id, nome, agora())

    async def verificar_steam(self, limite: int = 15) -> None:
        """Procura na Steam os jogos que ainda não conhecemos (ou cujo preço está velho)."""
        if self.steam is None:
            return
        async with self.verificando:
            for game_id, nome in self.db.radar_para_verificar(limite):
                if not eh_jogo(nome):
                    self.db.radar_salvar_jogo(montar_info(game_id, nome, None, None, agora()))
                    continue
                try:
                    info = await self.steam.info(game_id, nome, agora())
                except (ErroSteam, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                    log.warning("Steam indisponível (%s); tento de novo no próximo ciclo", exc)
                    return
                self.db.radar_salvar_jogo(info)
                await asyncio.sleep(PAUSA)

    async def enviar_alertas(self) -> None:
        for guild_id, canal_id in self.db.radar_servidores_com_alertas():
            canal = self.bot.get_channel(canal_id)
            if canal is None:
                continue
            _, preco_max = self.db.radar_config(guild_id)
            nomes = {r["login"]: r["nome"] for r in self.db.radar_streamers(guild_id)}
            vistos = self.db.radar_vistos(guild_id, dias=1)  # só o que apareceu de ontem pra hoje
            infos = self.db.radar_jogos(list({v[1] for v in vistos}))
            for s in ranquear(vistos, infos, datetime.now(timezone.utc).date(), preco_max, limite=50):
                if not self.db.radar_marcar_avisado(guild_id, s.info.game_id):
                    continue
                try:
                    await canal.send(embed=self.embed_alerta(s, nomes), view=self.view_alerta(s.info))
                except discord.HTTPException as exc:
                    log.warning("Não consegui avisar no canal %s: %s", canal_id, exc)

    async def importar_clipes(self, login: str, twitch_id: str) -> int:
        """Descobre o que o streamer jogou no último mês olhando os clipes mais vistos de cada semana."""
        assert self.twitch is not None
        fim = datetime.now(timezone.utc)
        clipes = []
        for _ in range(JANELA_DIAS // 7 + 1):
            inicio = fim - timedelta(days=7)
            clipes += await self.twitch.clipes(twitch_id, inicio, fim)
            fim = inicio
        ids = sorted({c["game_id"] for c in clipes if c.get("game_id")})
        nomes = {j["id"]: j["name"] for j in await self.twitch.jogos(ids)} if ids else {}
        jogos = set()
        for c in clipes:
            nome = nomes.get(c.get("game_id"))
            if nome and eh_jogo(nome):
                self.db.radar_registrar_visto(login, c["game_id"], nome, c["created_at"])
                jogos.add(c["game_id"])
        return len(jogos)

    def em_segundo_plano(self, coro) -> None:
        tarefa = asyncio.create_task(coro)
        self.tarefas.add(tarefa)
        tarefa.add_done_callback(self.tarefas.discard)

    # ---- embeds ----------------------------------------------------

    def embed_alerta(self, s: Sugestao, nomes: dict[str, str]) -> discord.Embed:
        quem = ", ".join(nomes.get(l, l) for l in s.dias_por_streamer)
        e = discord.Embed(title=f"📡 Novo no radar: {s.info.nome_exibicao}", url=s.info.url, color=COR_RADAR,
                          description=f"**{quem}** está jogando! Pode ser uma boa para a próxima maratona.\n\n"
                                      f"{linha_steam(s.info)}")
        if s.info.imagem:
            e.set_image(url=s.info.imagem)
        return e

    def view_alerta(self, info: InfoJogo) -> discord.ui.View:
        view = discord.ui.View(timeout=None)
        view.add_item(SalvarSugestaoBotao(info.game_id))
        if info.url:
            view.add_item(discord.ui.Button(label="Ver na Steam", url=info.url))
        return view

    # ---- permissões ------------------------------------------------

    @staticmethod
    def exigir_admin(interaction: discord.Interaction) -> None:
        perms = getattr(interaction.user, "guild_permissions", None)
        if not (perms and perms.manage_guild):
            raise ErroMaratona("Só quem tem a permissão **Gerenciar servidor** pode mudar o radar.")

    def exigir_twitch(self) -> TwitchClient:
        if self.twitch is None:
            raise ErroMaratona("O radar está desligado: faltam as chaves da Twitch no arquivo `.env` "
                               "(TWITCH_CLIENT_ID e TWITCH_CLIENT_SECRET). O README explica como pegar.")
        return self.twitch

    # ---- comandos --------------------------------------------------

    async def mostrar_radar(self, interaction: discord.Interaction) -> None:
        """Jogos para jogar com os amigos que os streamers andam jogando (botão do /o-que-jogar)."""
        streamers = self.db.radar_streamers(interaction.guild_id)
        if not streamers:
            raise ErroMaratona("Nenhum streamer no radar ainda. Um admin pode usar "
                               "`/radar adicionar-streamer` (por exemplo, `alanzoka`).")
        _, preco_max = self.db.radar_config(interaction.guild_id)
        preco_max = PRECO_MAX_PADRAO if preco_max is None else preco_max
        vistos = self.db.radar_vistos(interaction.guild_id)
        infos = self.db.radar_jogos(list({v[1] for v in vistos}))
        sugestoes = ranquear(vistos, infos, datetime.now(timezone.utc).date(), preco_max)
        nomes = {r["login"]: r["nome"] for r in streamers}

        lista_nomes = list(nomes.values())
        quem = lista_nomes[0] if len(lista_nomes) == 1 else ", ".join(lista_nomes[:-1]) + " e " + lista_nomes[-1]
        verbo = "jogou" if len(lista_nomes) == 1 else "jogaram"
        e = discord.Embed(
            title="📡 Radar de jogos", color=COR_RADAR,
            description=(f"Jogos **multiplayer** de até **R$ {preco_max / 100:.2f}**".replace(".", ",") +
                         f" que {quem} {verbo} nos últimos {JANELA_DIAS} dias "
                         "e que são bem avaliados na Steam."),
        )
        if not sugestoes:
            faltam = len(self.db.radar_para_verificar(1000))
            e.add_field(name="Nada por enquanto", value=(
                f"Ainda estou conferindo {plural(faltam, 'jogo', 'jogos')} na Steam. Tente de novo em alguns minutos."
                if faltam else "Nenhum jogo recente passou no filtro. Que tal acompanhar mais streamers?"))
            await interaction.response.send_message(embed=e)
            return

        for i, s in enumerate(sugestoes, start=1):
            link = f"\n[Ver na Steam]({s.info.url})" if s.info.url else ""
            e.add_field(name=f"{i}. {s.info.nome_exibicao}",
                        value=f"{linha_steam(s.info)}\n{linha_streamers(s, nomes)}{link}", inline=False)
        if sugestoes[0].info.imagem:
            e.set_thumbnail(url=sugestoes[0].info.imagem)
        e.set_footer(text="Use o menu abaixo para guardar jogos na sua lista da maratona.")
        await interaction.response.send_message(embed=e, view=SugestoesView(self.db, sugestoes))

    @radar.command(name="adicionar-streamer", description="Passa a acompanhar um canal da Twitch.")
    @app_commands.describe(canal="Nome do canal na Twitch (ex.: alanzoka) ou o link dele")
    async def cmd_adicionar(self, interaction: discord.Interaction, canal: str) -> None:
        self.exigir_admin(interaction)
        twitch = self.exigir_twitch()
        login = ler_login(canal)
        if not LOGIN_TWITCH.match(login):
            raise ErroMaratona(f"`{canal}` não parece um canal da Twitch.")
        await interaction.response.defer(thinking=True)
        try:
            usuarios = await twitch.usuarios([login])
            if not usuarios:
                raise ErroMaratona(f"Não achei o canal **{login}** na Twitch.")
            u = usuarios[0]
            ja_acompanhado = u["login"] in self.db.radar_todos_logins()
            if not self.db.radar_adicionar_streamer(interaction.guild_id, u["login"], u["id"], u["display_name"]):
                raise ErroMaratona(f"**{u['display_name']}** já está no radar deste servidor.")
            jogos = 0 if ja_acompanhado else await self.importar_clipes(u["login"], u["id"])
        except (ErroAPI, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ErroMaratona(f"Não consegui falar com a Twitch agora ({exc}). Tenta de novo daqui a pouco.")

        texto = f"✅ **{u['display_name']}** entrou no radar!"
        if jogos:
            texto += (f" Pelos clipes do último mês, achei **{plural(jogos, 'jogo', 'jogos')}**. "
                      "Estou conferindo cada um na Steam; em alguns minutos o radar (botão 📡 do `/o-que-jogar`) fica completo.")
        texto += f"\nDaqui pra frente, a cada {INTERVALO_MINUTOS} minutos eu vejo o que ele está jogando ao vivo."
        await interaction.followup.send(texto)
        self.em_segundo_plano(self.verificar_steam(limite=100))

    @radar.command(name="remover-streamer", description="Para de acompanhar um canal.")
    @app_commands.describe(canal="Canal que vai sair do radar")
    async def cmd_remover(self, interaction: discord.Interaction, canal: str) -> None:
        self.exigir_admin(interaction)
        if not self.db.radar_remover_streamer(interaction.guild_id, ler_login(canal)):
            raise ErroMaratona(f"**{canal}** não estava no radar.")
        await interaction.response.send_message(f"🗑️ **{canal}** saiu do radar.")

    @cmd_remover.autocomplete("canal")
    async def autocompletar_canal(self, interaction: discord.Interaction, atual: str):
        return [app_commands.Choice(name=r["nome"], value=r["login"])
                for r in self.db.radar_streamers(interaction.guild_id)
                if atual.lower() in r["login"]][:25]

    @radar.command(name="streamers", description="Lista os canais que o radar acompanha.")
    async def cmd_streamers(self, interaction: discord.Interaction) -> None:
        streamers = self.db.radar_streamers(interaction.guild_id)
        canal_alertas, preco_max = self.db.radar_config(interaction.guild_id)
        preco = (PRECO_MAX_PADRAO if preco_max is None else preco_max) / 100
        e = discord.Embed(title="📡 Quem está no radar", color=COR_RADAR, description=juntar(
            [f"• [{r['nome']}](https://twitch.tv/{r['login']})" for r in streamers],
            vazio="Ninguém ainda. Use `/radar adicionar-streamer`.", limite=4000))
        e.add_field(name="Preço máximo", value=f"R$ {preco:.2f}".replace(".", ","))
        e.add_field(name="Alertas", value=f"<#{canal_alertas}>" if canal_alertas else "Desligados")
        await interaction.response.send_message(embed=e, ephemeral=True)

    @radar.command(name="alertas", description="Escolhe um canal para avisar quando aparecer jogo novo no radar.")
    @app_commands.describe(canal="Canal dos avisos (deixe vazio para desligar)")
    async def cmd_alertas(self, interaction: discord.Interaction,
                          canal: Optional[discord.TextChannel] = None) -> None:
        self.exigir_admin(interaction)
        self.db.radar_definir_canal(interaction.guild_id, canal.id if canal else None)
        if canal is None:
            await interaction.response.send_message("🔕 Alertas do radar desligados.")
            return
        # O que já está no radar não vira alerta: só os jogos que aparecerem daqui pra frente.
        vistos = self.db.radar_vistos(interaction.guild_id)
        infos = self.db.radar_jogos(list({v[1] for v in vistos}))
        _, preco_max = self.db.radar_config(interaction.guild_id)
        for s in ranquear(vistos, infos, datetime.now(timezone.utc).date(), preco_max, limite=1000):
            self.db.radar_marcar_avisado(interaction.guild_id, s.info.game_id)
        await interaction.response.send_message(
            f"🔔 Quando um streamer do radar começar a jogar algo novo que passe no filtro, eu aviso em {canal.mention}.")

    @radar.command(name="preco-maximo", description="Muda o preço máximo dos jogos sugeridos.")
    @app_commands.describe(reais="Valor em reais (ex.: 40). Deixe vazio para voltar ao padrão de R$ 60.")
    async def cmd_preco(self, interaction: discord.Interaction,
                        reais: Optional[app_commands.Range[float, 0, 1000]] = None) -> None:
        self.exigir_admin(interaction)
        centavos = None if reais is None else int(round(reais * 100))
        self.db.radar_definir_preco(interaction.guild_id, centavos)
        valor = (PRECO_MAX_PADRAO if centavos is None else centavos) / 100
        await interaction.response.send_message(f"💰 Preço máximo do radar: **R$ {valor:.2f}**".replace(".", ","))


    # ---- sugestões da loja (só Steam) --------------------------------

    async def itens_da_loja(self, tipo: str) -> list[ItemLoja]:
        """Busca na Steam (ou pega da memória, se a busca tem menos de 6 horas)."""
        async with self.buscando_loja:
            guardado = self.cache_loja.get(tipo)
            if guardado and time.monotonic() - guardado[0] < CACHE_LOJA_SEGUNDOS:
                return guardado[1]
            itens: list[ItemLoja] = []
            for busca in BUSCAS_LOJA:
                itens += await self.steam.busca_loja({**TIPOS[tipo], **busca})
                await asyncio.sleep(PAUSA)
            self.cache_loja[tipo] = (time.monotonic(), itens)
            return itens

    async def mostrar_sugestoes(self, interaction: discord.Interaction, chave: str = "em_alta") -> None:
        """Friendslop da loja da Steam: "em_alta", "novidades" ou "promocoes" (botões do /o-que-jogar)."""
        await interaction.response.defer(thinking=True)
        try:
            itens = await self.itens_da_loja(chave)
        except (ErroSteam, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ErroMaratona(f"A Steam não respondeu agora ({exc}). Tenta de novo daqui a pouco.")
        _, preco_max = self.db.radar_config(interaction.guild_id)
        preco_max = PRECO_MAX_PADRAO if preco_max is None else preco_max
        friendslop = filtrar(itens, preco_max)
        if not friendslop:
            await interaction.followup.send("Não achei nenhum jogo que passe no filtro agora. 😕 "
                                            "Tenta outro tipo, ou peça para um admin aumentar o preço com `/radar preco-maximo`.")
            return
        view = SugestoesLojaView(self, friendslop, chave, preco_max)
        await interaction.followup.send(embed=view.embed(), view=view)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(RadarCog(bot))
