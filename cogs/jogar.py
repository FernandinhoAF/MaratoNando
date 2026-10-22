"""/o-que-jogar: "estamos em 4, o que dá para jogar?" (com ou sem maratona).

  • Junta quem vai jogar: as pessoas marcadas, ou quem está na sua call.
  • 🎮 Do que vocês têm: jogos das bibliotecas da Steam (e das listas) que aceitam esse
    número de pessoas, do mais barato para o grupo ao mais caro.
  • 🆕 Novidades: friendslop em alta na loja que aceita esse número de pessoas.

Quantas pessoas cada jogo aceita vem da descrição da Steam (jogadores.py). Quando a
descrição não diz, ou diz errado, qualquer um ensina com /jogo jogadores.
"""

import asyncio
import logging
import re
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from biblioteca import Opcao, app_da_linha, calcular_opcoes, escolher_candidatos, reais
from cogs.maratona import SEM_PING, autocompletar_jogo, juntar, plural, tratar_erro
from db import Database, ErroMaratona
from jogadores import cabe, faixa_texto
from marca import COR_DETALHE, url_nando
from nomes import ler_escolha
from radar import PRECO_MAX_PADRAO
from servicos.steam import ErroSteam
from sugestao import filtrar

log = logging.getLogger("maratonando.jogar")

MOSTRAR = 5            # jogos por seção
NOVIDADES_ANALISAR = 15  # quantos friendslop da loja o bot confere a descrição (1 por segundo)


def faixa_da_linha(row) -> Optional[tuple[int, int]]:
    if row is None or row["maximo"] is None:
        return None
    return row["minimo"], row["maximo"]


def custo_texto(o: Opcao) -> str:
    """O pedaço de "quanto custa para o grupo" de cada linha."""
    if o.todos_tem:
        return "✅ todo mundo tem"
    if o.app.gratis:
        return "🆓 grátis"
    tem = f"{len(o.donos)} {'tem' if len(o.donos) == 1 else 'têm'}" if o.donos else "ninguém tem"
    return f"{tem} · 🛒 {reais(o.custo)} para o resto"


class JogarCog(commands.Cog):
    jogo = app_commands.Group(name="jogo", description="Informações sobre os jogos.", guild_only=True)

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]

    async def cog_app_command_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    # ---- quem vai jogar ---------------------------------------------

    @staticmethod
    def quem_vai(interaction: discord.Interaction, marcados: Optional[str]) -> tuple[list[int], str]:
        """(ids das pessoas, de onde vieram). Marcados > quem está na sua call > ninguém."""
        ids = list(dict.fromkeys(int(i) for i in re.findall(r"<@!?(\d+)>", marcados or "")))
        if ids:
            return ids, "marcadas"
        voz = getattr(getattr(interaction.user, "voice", None), "channel", None)
        if voz is not None:
            return [m.id for m in voz.members if not m.bot], f"na call {voz.mention}"
        return [], ""

    # ---- /o-que-jogar -------------------------------------------------

    @app_commands.command(name="o-que-jogar",
                          description="Sugere jogos para o número exato de pessoas que vão jogar agora.")
    @app_commands.describe(
        pessoas="Quantas pessoas vão jogar (vazio = quem está na sua call, ou quem você marcar)",
        jogadores="Marque quem vai jogar (@Ana @Bia…) para o bot olhar a Steam de cada um",
        preco_max="Preço máximo das novidades da loja, em reais (padrão: o do /radar)",
    )
    @app_commands.guild_only()
    async def cmd_o_que_jogar(self, interaction: discord.Interaction,
                              pessoas: Optional[app_commands.Range[int, 1, 32]] = None,
                              jogadores: Optional[str] = None,
                              preco_max: Optional[app_commands.Range[int, 0, 500]] = None) -> None:
        ids, origem = self.quem_vai(interaction, jogadores)
        n = pessoas or len(ids)
        if n == 0:
            raise ErroMaratona("Quantas pessoas vão jogar? Preencha `pessoas`, marque o pessoal em `jogadores` "
                               "ou entre numa call antes de usar o comando.")
        if preco_max is None:
            _, guardado = self.db.radar_config(interaction.guild_id)
            preco_centavos = PRECO_MAX_PADRAO if guardado is None else guardado
        else:
            preco_centavos = preco_max * 100  # o bot guarda preços em centavos
        await interaction.response.defer(thinking=True)

        steam = self.bot.get_cog("SteamCog")
        if steam is None:
            raise ErroMaratona("A parte da Steam do bot não está ligada.")

        tem, talvez, aviso_bib = await self.do_que_tem(steam, ids, n)
        ja_mostrados = {o.app.appid for o in tem} | {o.app.appid for o in talvez}
        novidades = await self.novidades(steam, n, preco_centavos, ja_mostrados)

        quem = f" ({plural(len(ids), 'pessoa', 'pessoas')} {origem})" if ids and origem else ""
        e = discord.Embed(title=f"🎮 O que jogar com {plural(n, 'pessoa', 'pessoas')}?", color=COR_DETALHE)
        e.description = (f"Jogos que aceitam **{n}**{quem}." if n > 1
                         else "Jogos para jogar sozinho (ou para esperar o resto do pessoal chegar). 😅")
        e.add_field(name="🎮 Do que vocês já têm (mais barato para o grupo primeiro)",
                    value=juntar(self.linhas(tem), vazio=aviso_bib or "Nenhum jogo de vocês serve para esse número. 😕"),
                    inline=False)
        if talvez:
            e.add_field(name="❔ Talvez (a Steam não diz quantas pessoas aceita)",
                        value=juntar([f"**[{o.app.nome}]({o.app.url})** · {custo_texto(o)}" for o in talvez[:3]]
                                     + ["Sabe quantos jogam? Ensine o bot com `/jogo jogadores`."]),
                        inline=False)
        e.add_field(name="🆕 Novidades friendslop da loja que servem",
                    value=juntar(novidades, vazio="Nenhuma novidade da loja serve para esse número agora."),
                    inline=False)
        e.set_thumbnail(url=url_nando(self.bot))
        e.set_footer(text="O nº de jogadores vem da descrição da Steam. Errado ou faltando? /jogo jogadores")
        await interaction.followup.send(embed=e, allowed_mentions=SEM_PING)

    def linhas(self, opcoes: list[Opcao]) -> list[str]:
        faixas = self.db.jogadores_de([o.app.appid for o in opcoes])
        return [f"**[{o.app.nome}]({o.app.url})** · {faixa_texto(faixa_da_linha(faixas.get(o.app.appid)))} · "
                f"{custo_texto(o)}" for o in opcoes]

    async def do_que_tem(self, steam, ids: list[int], n: int) -> tuple[list[Opcao], list[Opcao], str]:
        """(jogos que servem, jogos que talvez sirvam, aviso se não deu para olhar as bibliotecas)."""
        if steam.api is None:
            return [], [], "A chave da Steam não está configurada, então não dá para ver o que vocês têm."
        if not ids:
            return [], [], ("Marque quem vai jogar (ou entrem numa call) para o bot olhar as bibliotecas "
                            "da Steam de vocês.")
        try:
            await steam.atualizar_bibliotecas(ids)
            bibliotecas = self.db.bibliotecas(ids)
            if not bibliotecas:
                return [], [], "Ninguém aqui tem a Steam vinculada (com jogos públicos). Use `/steam vincular`."
            candidatos = escolher_candidatos(bibliotecas, self.db.appids_das_listas_de(ids))
            await steam.analisar_na_loja(candidatos, 20)
            apps = {a: app_da_linha(r) for a, r in self.db.apps(candidatos).items()}
            opcoes = calcular_opcoes(bibliotecas, apps, candidatos)
        except Exception as exc:  # Steam fora do ar etc.: mostra o resto do mesmo jeito
            log.warning("Não consegui olhar as bibliotecas: %s", exc)
            return [], [], "A Steam não respondeu agora para ver as bibliotecas. 😕"
        faixas = self.db.jogadores_de([o.app.appid for o in opcoes])
        servem = [o for o in opcoes if cabe(faixa_da_linha(faixas.get(o.app.appid)), n)]
        talvez = [o for o in opcoes if o.app.appid in faixas and faixa_da_linha(faixas[o.app.appid]) is None]
        return servem[:MOSTRAR], talvez, ""

    async def novidades(self, steam, n: int, preco_max: int, pular: set[int]) -> list[str]:
        """preco_max em centavos."""
        radar = self.bot.get_cog("RadarCog")
        if radar is None:
            return []
        try:
            itens = filtrar(await radar.itens_da_loja("em_alta"), preco_max)
            itens = [i for i in itens if i.appid not in pular][:NOVIDADES_ANALISAR]
            await steam.analisar_na_loja([i.appid for i in itens], NOVIDADES_ANALISAR)
        except (ErroSteam, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            log.warning("Não consegui buscar as novidades: %s", exc)
            return ["A Steam não respondeu agora. 😕"]
        faixas = self.db.jogadores_de([i.appid for i in itens])
        linhas = []
        for i in itens:
            faixa = faixa_da_linha(faixas.get(i.appid))
            if cabe(faixa, n):
                preco = "grátis" if i.gratis else (i.preco_texto or reais(i.preco_centavos or 0))
                desconto = f" (−{i.desconto}%)" if i.desconto else ""
                linhas.append(f"**[{i.nome}]({i.url})** · {faixa_texto(faixa)} · {preco}{desconto}")
        return linhas[:MOSTRAR]

    # ---- /jogo jogadores ---------------------------------------------

    @jogo.command(name="jogadores", description="Mostra ou ensina ao bot quantas pessoas um jogo aceita.")
    @app_commands.describe(jogo="Nome do jogo (aparecem sugestões da Steam enquanto digita)",
                           maximo="Máximo de jogadores (vazio = só mostrar o que o bot sabe)",
                           minimo="Mínimo de jogadores (padrão: 1)")
    async def cmd_jogadores(self, interaction: discord.Interaction, jogo: str,
                            maximo: Optional[app_commands.Range[int, 1, 100]] = None,
                            minimo: Optional[app_commands.Range[int, 1, 100]] = None) -> None:
        nome, appid = ler_escolha(jogo)
        steam = self.bot.get_cog("SteamCog")
        if not appid and steam is not None:
            achado = await steam.reconhecer(nome)
            if achado:
                appid, nome = achado
        if not appid:
            raise ErroMaratona(f"Não achei **{nome}** na Steam. Escolha o jogo da lista de sugestões que "
                               "aparece enquanto você digita.")
        if maximo is None:
            if steam is not None:
                await steam.analisar_na_loja([appid], 1)
            row = self.db.jogadores_de([appid]).get(appid)
            faixa = faixa_da_linha(row)
            if faixa is None:
                texto = (f"Ainda não sei quantas pessoas jogam **{nome}**: a descrição da Steam não diz. "
                         f"Ensine com `/jogo jogadores jogo:{nome} maximo:…`.")
            else:
                fonte = (f"ensinado por <@{row['definido_por']}>" if row["fonte"] == "grupo"
                         else "lido da descrição da Steam")
                texto = f"**{nome}**: {faixa_texto(faixa)} ({fonte})."
            await interaction.response.send_message(texto, ephemeral=True, allowed_mentions=SEM_PING)
            return
        self.db.definir_jogadores_grupo(appid, minimo or 1, maximo, interaction.user.id)
        await interaction.response.send_message(
            f"✅ Anotado: **{nome}** é de {faixa_texto((minimo or 1, maximo))}. "
            "O `/o-que-jogar` já usa isso para todo mundo.", allowed_mentions=SEM_PING)

    @cmd_jogadores.autocomplete("jogo")
    async def _autocompletar(self, interaction: discord.Interaction, atual: str):
        return await autocompletar_jogo(self.bot, interaction, atual)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JogarCog(bot))
