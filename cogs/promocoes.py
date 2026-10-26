"""Acompanha os preços dos jogos das listas na Steam, em silêncio.

A cada 6 horas (e uma vez logo que o bot liga):
  1. Cada nome de jogo das listas é procurado na Steam (só uma vez; o resultado fica guardado).
  2. O bot confere o preço atual de cada jogo, um por vez, com pausa entre eles, e guarda quem
     está em promoção. Quando a promoção acaba, ele esquece dela.

Isso aparece só quando alguém olha: a /lista mostra "💸 −38%" nos jogos em promoção.
O bot NÃO manda mensagem avisando (antes mandava, marcando quem tinha o jogo na lista, mas
virava spam no canal; foi tirado em out/2026).
"""

import asyncio
import logging
from typing import Optional

import aiohttp
import discord
from discord.ext import commands, tasks

from db import Database
from radar import escolher_resultado
from servicos.steam import PAUSA, ErroSteam, SteamClient

log = logging.getLogger("maratonando.promocoes")

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
        """Anota em quais servidores cada pessoa usa o bot (para a lista de desejos do grupo e o /jogo)."""
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
                    self.db.atualizar_promocao(appid, nome, desconto, (preco or {}).get("final_formatted"),
                                               (preco or {}).get("initial_formatted"))
            except (ErroSteam, aiohttp.ClientError, asyncio.TimeoutError) as exc:
                log.warning("Steam indisponível (%s); tento de novo no próximo ciclo", exc)
                return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PromocoesCog(bot))
