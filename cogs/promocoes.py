"""Avisa quando um jogo das listas pessoais (ou um desejo do grupo) entra em promoção na Steam.

Como funciona (a cada 6 horas, e uma vez logo que o bot liga):
  1. Cada nome de jogo das listas é procurado na Steam (só uma vez; o resultado fica guardado).
  2. O bot confere o preço atual de cada jogo, um por vez, com pausa entre eles.
  3. Se um jogo entrou em promoção (ou o desconto aumentou), o bot avisa no canal do bot
     (/config canal-do-bot) de cada servidor onde alguém que tem esse jogo na lista usa o bot.
     O aviso marca (com notificação) só quem tem o jogo na lista. Nada de DM.
  4. Quando a promoção acaba, o bot esquece dela; se o jogo entrar em promoção de novo, avisa de novo.

Desejo do grupo = jogo da lista de desejos da Steam que 2 ou mais pessoas do mesmo servidor querem
(contando a /lista também). Esses entram no aviso, marcando todo mundo que quer. Um jogo que só uma
pessoa tem na lista de desejos da Steam não é avisado aqui (a própria Steam já manda e-mail disso).
"""

import asyncio
import logging
from typing import Optional

import aiohttp
import discord
from discord.ext import commands, tasks

from marca import COR_FIM
from db import Database
from radar import escolher_resultado
from servicos.steam import PAUSA, ErroSteam, SteamClient

log = logging.getLogger("maratonando.promocoes")

COR_PROMO = COR_FIM  # verde (Manual da Marca)
INTERVALO_HORAS = 6


class PromocoesCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]
        self.session: Optional[aiohttp.ClientSession] = None
        self.steam: Optional[SteamClient] = None
        self.verificando = asyncio.Lock()

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.steam = SteamClient(self.session)
        self.ciclo.start()

    async def cog_unload(self) -> None:
        self.ciclo.cancel()
        if self.session:
            await self.session.close()

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Anota em quais servidores cada pessoa usa o bot (é lá que as promoções dela são avisadas)."""
        if interaction.guild_id is not None:
            self.db.registrar_usuario_servidor(interaction.user.id, interaction.guild_id)

    @tasks.loop(hours=INTERVALO_HORAS)
    async def ciclo(self) -> None:
        try:
            await self.verificar()
        except Exception:
            log.exception("Erro ao verificar promoções")

    @ciclo.before_loop
    async def antes_do_ciclo(self) -> None:
        await self.bot.wait_until_ready()

    async def verificar(self) -> None:
        async with self.verificando:
            try:
                # 1. Descobre qual jogo da Steam é cada nome novo das listas.
                for nome in self.db.nomes_sem_steam():
                    self.db.salvar_steam_nome(nome, escolher_resultado(nome, await self.steam.buscar(nome)))
                    await asyncio.sleep(PAUSA)

                # 2. Confere os preços (das listas pessoais e dos desejos do grupo).
                avisos: list[tuple[int, str]] = []
                for appid, nome in self.db.appids_das_listas().items():
                    if not nome:  # jogo da lista de desejos da Steam que o bot ainda não sabe o nome
                        detalhes = await self.steam.detalhes(appid)
                        await asyncio.sleep(PAUSA)
                        if not detalhes or not detalhes.get("name"):
                            continue
                        nome = detalhes["name"]
                        self.db.catalogo_adicionar([(appid, nome)])
                    preco = await self.steam.preco(appid)
                    await asyncio.sleep(PAUSA)
                    desconto = int((preco or {}).get("discount_percent") or 0)
                    tipo = self.db.atualizar_promocao(appid, nome, desconto, (preco or {}).get("final_formatted"),
                                                      (preco or {}).get("initial_formatted"))
                    if tipo:
                        avisos.append((appid, tipo))
            except (ErroSteam, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log.warning("Steam indisponível (%s); tento de novo no próximo ciclo", exc)
                return

        # 3. Avisa.
        for appid, tipo in avisos:
            await self.anunciar(appid, tipo)

    def embed_promocao(self, promo, tipo: str, querem: int = 1) -> discord.Embed:
        titulo = "ficou ainda mais barato" if tipo == "maior" else "está em promoção"
        preco = f"**{promo['preco_texto']}** (−{promo['desconto']}%)"
        if promo["original_texto"]:
            preco = f"~~{promo['original_texto']}~~ → {preco}"
        e = discord.Embed(
            title=f"💸 {promo['nome']} {titulo}!", color=COR_PROMO,
            url=f"https://store.steampowered.com/app/{promo['appid']}/",
            description=f"{preco}\n\nBoa hora de garantir antes da próxima maratona! 🎮",
        )
        if querem >= 2:
            e.add_field(name="🔥 Desejo do grupo", value=f"{querem} pessoas daqui querem esse jogo "
                                                          "(na /lista ou na lista de desejos da Steam).")
        return e

    @staticmethod
    def texto_marcando(pessoas: list[int]) -> str:
        mencoes = " ".join(f"<@{p}>" for p in pessoas)
        if len(pessoas) == 1:
            return f"{mencoes}, um jogo da sua lista entrou em promoção! 💸"
        return f"{mencoes}, um jogo que vocês querem entrou em promoção! 💸"

    async def anunciar(self, appid: int, tipo: str) -> None:
        promo = self.db.promocao(appid)
        if promo is None:
            return
        for guild_id, canal_id in self.db.servidores_para_promocoes():
            pessoas = self.db.interessados(guild_id, appid)
            canal = self.bot.get_channel(canal_id)
            if not pessoas or canal is None:
                continue
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="Ver na Steam", url=f"https://store.steampowered.com/app/{appid}/"))
            try:
                # Notifica só as pessoas que têm o jogo na lista (nunca @everyone nem cargos).
                await canal.send(
                    self.texto_marcando(pessoas), embed=self.embed_promocao(promo, tipo, len(pessoas)), view=view,
                    allowed_mentions=discord.AllowedMentions(
                        everyone=False, roles=False, users=[discord.Object(id=p) for p in pessoas]),
                )
            except discord.HTTPException as exc:
                log.warning("Não consegui avisar a promoção no canal %s: %s", canal_id, exc)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PromocoesCog(bot))
