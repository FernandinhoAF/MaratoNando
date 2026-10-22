"""Jogos grátis para resgatar: o grátis da semana da Epic e os 100% off da Steam.

  • /gratis mostra o que está grátis agora (e o que fica grátis em breve na Epic).
  • A cada 3 horas o bot confere e, quando aparece jogo grátis novo, avisa no canal do bot
    (sem marcar ninguém). Liga/desliga com /config gratis.
"""

import asyncio
import logging
import time
from datetime import datetime, timezone

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands, tasks

import gratis
from cogs.maratona import SEM_PING, tratar_erro
from db import Database, ErroMaratona
from gratis import Brinde
from marca import COR_FIM, url_nando
from servicos.epic import EpicClient
from servicos.steam import SteamClient

log = logging.getLogger("maratonando.gratis")

INTERVALO_HORAS = 3
CACHE_SEGUNDOS = 30 * 60


def carimbo(iso: str, estilo: str) -> str:
    return f"<t:{int(datetime.fromisoformat(iso).timestamp())}:{estilo}>"


def embed_brinde(b: Brinde) -> discord.Embed:
    loja = "Epic Games" if b.loja == "epic" else "Steam"
    if b.fim:
        quando = f"Grátis na **{loja}** até {carimbo(b.fim, 'f')} ({carimbo(b.fim, 'R')})"
    else:
        quando = f"Grátis na **{loja}** por tempo limitado"
    custa = f"\nNormalmente custa ~~{b.preco_original}~~" if b.preco_original else ""
    e = discord.Embed(title=f"🎁 {b.nome}", url=b.url, color=COR_FIM,
                      description=f"{quando}{custa}\nResgatando, o jogo é seu para sempre.")
    if b.imagem:
        e.set_image(url=b.imagem)
    return e


class GratisCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]
        self.session: aiohttp.ClientSession | None = None
        self.cache: tuple[float, tuple[list[Brinde], list[Brinde], list[Brinde]]] | None = None
        self.buscando = asyncio.Lock()

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.epic = EpicClient(self.session)
        self.steam = SteamClient(self.session)
        self.ciclo.start()

    async def cog_unload(self) -> None:
        self.ciclo.cancel()
        if self.session:
            await self.session.close()

    async def cog_app_command_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    async def buscar(self) -> tuple[list[Brinde], list[Brinde], list[Brinde]]:
        """(Epic grátis agora, Epic grátis em breve, Steam 100% off). Cada loja que falhar vem vazia."""
        async with self.buscando:
            if self.cache and time.monotonic() - self.cache[0] < CACHE_SEGUNDOS:
                return self.cache[1]
            agora, em_breve, steam = [], [], []
            try:
                agora, em_breve = gratis.ler_epic(await self.epic.jogos_gratis(), datetime.now(timezone.utc))
            except Exception as exc:
                log.warning("Não consegui ler os grátis da Epic: %s", exc)
            try:
                steam = gratis.ler_steam(await self.steam.gratis_para_sempre())
            except Exception as exc:
                log.warning("Não consegui ler os grátis da Steam: %s", exc)
            resultado = (agora, em_breve, steam)
            self.cache = (time.monotonic(), resultado)
            return resultado

    # ---- /gratis ------------------------------------------------------

    @app_commands.command(name="gratis", description="Jogos grátis para resgatar agora (Epic e Steam) e os próximos.")
    @app_commands.guild_only()
    async def cmd_gratis(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        agora, em_breve, steam = await self.buscar()
        brindes = agora + steam
        if not brindes and not em_breve:
            raise ErroMaratona("Não achei nenhum jogo grátis agora (ou as lojas não responderam). 😕")
        embeds = [embed_brinde(b) for b in brindes][:9]
        if em_breve:
            linhas = [f"• **[{b.nome}]({b.url})** — de {carimbo(b.inicio, 'f')} até {carimbo(b.fim, 'f')}"
                      for b in em_breve[:6]]
            proximos = discord.Embed(title="🔜 Grátis em breve na Epic", color=COR_FIM, description="\n".join(linhas))
            proximos.set_thumbnail(url=url_nando(self.bot))
            embeds.append(proximos)
        texto = ("🎁 **Jogos grátis para resgatar** — resgatou, é seu para sempre!" if brindes
                 else "🎁 Nenhum jogo grátis para resgatar **agora**, mas olha o que vem aí:")
        await interaction.followup.send(texto, embeds=embeds, allowed_mentions=SEM_PING)

    # ---- aviso automático -------------------------------------------

    @tasks.loop(hours=INTERVALO_HORAS)
    async def ciclo(self) -> None:
        try:
            await self.avisar()
        except Exception:
            log.exception("Erro ao avisar jogos grátis")

    @ciclo.before_loop
    async def antes_do_ciclo(self) -> None:
        await self.bot.wait_until_ready()

    async def avisar(self) -> None:
        servidores = self.db.servidores_para_gratis()
        if not servidores:
            return
        self.cache = None  # sempre dados novos no aviso
        agora, _, steam = await self.buscar()
        brindes = {b.chave: b for b in agora + steam}
        for guild_id, canal_id in servidores:
            novas = self.db.gratis_novos(guild_id, list(brindes))
            if not novas:
                continue
            canal = self.bot.get_partial_messageable(canal_id, guild_id=guild_id)
            try:
                await canal.send("🎁 **Jogo grátis para resgatar!** Resgatou, é seu para sempre:",
                                 embeds=[embed_brinde(brindes[c]) for c in novas][:10], allowed_mentions=SEM_PING)
            except discord.HTTPException as exc:
                log.warning("Não consegui avisar os grátis no servidor %s: %s", guild_id, exc)
                continue
            self.db.marcar_gratis_avisados(guild_id, novas)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(GratisCog(bot))
