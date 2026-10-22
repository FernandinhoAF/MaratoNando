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

import ficha
from biblioteca import Opcao, app_da_linha, calcular_opcoes, escolher_candidatos, reais
from cogs.maratona import SEM_PING, autocompletar_jogo, juntar, plural, texto_resultado, tratar_erro
from notas import estrelas, numero
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


class FichaView(discord.ui.View):
    """Botões da ficha: salvar na lista, colocar na roleta (se tiver maratona aberta) e abrir na Steam."""

    def __init__(self, cog: "JogarCog", appid: int, nome: str, com_roleta: bool) -> None:
        super().__init__(timeout=1800)
        self.cog, self.appid, self.nome = cog, appid, nome
        if not com_roleta:
            self.remove_item(self.roleta)
        self.add_item(discord.ui.Button(label="Ver na Steam", emoji="🛒",
                                        url=f"https://store.steampowered.com/app/{appid}/"))

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    @discord.ui.button(label="Salvar na minha lista", emoji="📝", style=discord.ButtonStyle.secondary)
    async def lista(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        db = self.cog.db
        db.salvar_steam_nome(self.nome, self.appid)  # a lista sabe qual jogo da Steam é (avisos de promoção)
        salvos, recusados = db.salvar_na_lista(interaction.user.id, [self.nome])
        if interaction.guild_id:
            db.registrar_usuario_servidor(interaction.user.id, interaction.guild_id)
        texto = (f"📝 **{self.nome}** está na sua lista. Na próxima maratona ele aparece em **Adicionar jogos**."
                 if salvos else f"**{self.nome}**: {recusados[0][1]}.")
        await interaction.response.send_message(texto, ephemeral=True)

    @discord.ui.button(label="Colocar na roleta", emoji="➕", style=discord.ButtonStyle.success)
    async def roleta(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        db = self.cog.db
        m = db.maratona_aberta(interaction.guild_id)
        if m is None:
            raise ErroMaratona("A maratona já acabou. Use **Salvar na minha lista** para guardar para a próxima.")
        adicionados, recusados = db.adicionar_jogos(m.id, interaction.user.id, [self.nome], {self.nome: self.appid})
        await interaction.response.send_message(texto_resultado(adicionados, recusados, "Na roleta"), ephemeral=True)
        maratona_cog = self.cog.bot.get_cog("MaratonaCog")
        if adicionados and maratona_cog is not None:
            await maratona_cog.atualizar_painel(m.id)


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

    # ---- /jogo ver ----------------------------------------------------

    @jogo.command(name="ver", description="Ficha do jogo: preço, avaliações, modos, nº de jogadores e quem do servidor tem.")
    @app_commands.describe(jogo="Nome do jogo (aparecem sugestões da Steam enquanto digita)")
    async def cmd_ver(self, interaction: discord.Interaction, jogo: str) -> None:
        nome, appid = ler_escolha(jogo)
        steam = self.bot.get_cog("SteamCog")
        if steam is None:
            raise ErroMaratona("A parte da Steam do bot não está ligada.")
        await interaction.response.defer(thinking=True)
        if not appid:
            achado = await steam.reconhecer(nome)
            if achado is None:
                parecidos = await steam.parecidos(nome)
                if not parecidos:
                    raise ErroMaratona(f"Não achei **{nome}** na Steam. 😕")
                appid, nome = parecidos[0]
            else:
                appid, nome = achado
        try:
            detalhes = await steam.loja.detalhes(appid, idioma="brazilian")
            avaliacoes = await steam.loja.avaliacoes(appid) if detalhes else None
            await steam.analisar_na_loja([appid], 1)  # guarda preço e nº de jogadores (em inglês)
        except (ErroSteam, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ErroMaratona(f"A Steam não respondeu agora ({exc}). Tenta de novo daqui a pouco.")
        if not detalhes:
            raise ErroMaratona(f"A Steam não tem a página de **{nome}** no Brasil. 😕")

        nome = detalhes.get("name") or nome
        e = discord.Embed(title=f"🎮 {nome}", url=f"https://store.steampowered.com/app/{appid}/",
                          color=COR_DETALHE, description=ficha.descricao_curta(detalhes) or None)
        if detalhes.get("header_image"):
            e.set_image(url=detalhes["header_image"])
        e.add_field(name="💰 Preço", value=ficha.preco_texto(detalhes))
        e.add_field(name="👍 Avaliações", value=ficha.avaliacao_texto(avaliacoes))
        faixa = faixa_da_linha(self.db.jogadores_de([appid]).get(appid))
        e.add_field(name="👥 Jogadores", value=faixa_texto(faixa) if faixa
                    else "A Steam não diz (ensine com `/jogo jogadores`)")
        modos = ficha.modos_de_jogo(detalhes)
        if modos:
            e.add_field(name="🕹️ Modos", value=" · ".join(modos))
        extras = [x for x in (" · ".join(ficha.generos(detalhes)), ficha.lancamento(detalhes)) if x]
        if extras:
            e.add_field(name="🏷️ Gênero e lançamento", value="\n".join(extras))

        e.add_field(name="🏠 No servidor", value=await self.no_servidor(steam, interaction.guild_id, appid, detalhes),
                    inline=False)
        e.set_footer(text="Preço e avaliações da Steam Brasil · o resto é do próprio grupo")
        aberta = self.db.maratona_aberta(interaction.guild_id) is not None
        await interaction.followup.send(embed=e, view=FichaView(self, appid, nome, aberta), allowed_mentions=SEM_PING)

    async def no_servidor(self, steam, guild_id: int, appid: int, detalhes: dict) -> str:
        """Quem tem, quanto custa para o resto, quem quer (lista) e como foi nas maratonas."""
        linhas = []
        ids = self.db.usuarios_com_steam(guild_id)
        if ids and steam.api is not None:
            try:
                await steam.atualizar_bibliotecas(ids)
            except Exception as exc:  # biblioteca velha serve: só não fica atualizada
                log.info("Não consegui atualizar as bibliotecas: %s", exc)
            bibliotecas = self.db.bibliotecas(ids)
            donos = [u for u, jogos in bibliotecas.items() if appid in jogos]
            faltam = [u for u in bibliotecas if u not in donos]
            if donos:
                linhas.append(f"✅ **Têm ({len(donos)}):** " + " ".join(f"<@{u}>" for u in donos))
            if faltam:
                preco = (detalhes.get("price_overview") or {}).get("final")
                custo = "" if detalhes.get("is_free") or preco is None else f" · {reais(preco * len(faltam))} para todos"
                linhas.append(f"🛒 **Não têm ({len(faltam)}):** " + " ".join(f"<@{u}>" for u in faltam) + custo)
        else:
            linhas.append("Ninguém daqui tem a Steam vinculada (`/steam vincular`), então não sei quem tem.")
        querem = self.db.na_lista_de(guild_id, appid)
        if querem:
            linhas.append(f"📝 **Na lista de:** " + " ".join(f"<@{u}>" for u in querem))
        historico = ficha.nota_do_grupo(self.db.notas_do_servidor(guild_id), appid)
        if historico:
            media, quantas, vezes = historico
            nota = f" · ⭐ {estrelas(media)} {numero(media)} ({plural(quantas, 'nota', 'notas')})" if quantas else ""
            linhas.append(f"🎰 Saiu em {plural(vezes, 'maratona', 'maratonas')}{nota}")
        return juntar(linhas)

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
