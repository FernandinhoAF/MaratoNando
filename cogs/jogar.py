"""Comandos de jogos fora da maratona: /o-que-jogar, /jogo e /perfil.

/o-que-jogar: "estamos em 4, o que dá para jogar?" (com ou sem maratona).
  • Junta quem vai jogar: as pessoas marcadas, ou quem está na sua call (ou o número do menu).
  • 🎮 Do que vocês têm: jogos das bibliotecas da Steam (e das listas) que aceitam esse
    número de pessoas, do mais barato para o grupo ao mais caro.
  • 🆕 Novidades: friendslop em alta na loja que aceita esse número de pessoas.
  • Botões para as outras sugestões: 🔥 em alta / 🆕 novidades / 💸 promoções da Steam,
    🎁 jogos grátis e 📡 radar dos streamers.

Quantas pessoas cada jogo aceita vem da descrição da Steam (jogadores.py). Quando a
descrição não diz, ou diz errado, qualquer um ensina pelo botão 👥 da ficha do /jogo.

/jogo: ficha do jogo, com botões de lista, roleta e nº de jogadores.
/perfil: perfil gamer, com os botões para vincular ou desvincular a Steam.
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
import perfil
from biblioteca import Opcao, app_da_linha, calcular_opcoes, escolher_candidatos, reais
from cogs.maratona import SEM_PING, autocompletar_jogo, juntar, plural, texto_resultado, tratar_erro
from notas import estrelas, numero
from db import Database, ErroMaratona
from jogadores import cabe, faixa_texto
from marca import COR_DETALHE, COR_PAINEL, url_nando
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


class JogadoresModal(discord.ui.Modal, title="Quantas pessoas jogam?"):
    minimo = discord.ui.TextInput(label="Mínimo de jogadores", placeholder="1", required=False, max_length=3)
    maximo = discord.ui.TextInput(label="Máximo de jogadores", placeholder="4", max_length=3)

    def __init__(self, cog: "JogarCog", appid: int, nome: str) -> None:
        super().__init__(title=f"Quantas pessoas jogam {nome}?"[:45])
        self.cog, self.appid, self.nome = cog, appid, nome

    async def on_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        try:
            minimo = int(self.minimo.value or 1)
            maximo = int(self.maximo.value)
        except ValueError:
            raise ErroMaratona("Escreva só números (ex.: mínimo 1, máximo 4).") from None
        if maximo > 100:
            raise ErroMaratona("Máximo de 100 jogadores.")
        self.cog.db.definir_jogadores_grupo(self.appid, minimo, maximo, interaction.user.id)
        await interaction.response.send_message(
            f"✅ Anotado: **{self.nome}** é de {faixa_texto((minimo, maximo))}. "
            "O `/o-que-jogar` já usa isso para todo mundo.", allowed_mentions=SEM_PING)


class FichaView(discord.ui.View):
    """Botões da ficha: salvar na lista, colocar na roleta (se tiver maratona aberta), corrigir o
    número de jogadores e abrir na Steam."""

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

    @discord.ui.button(label="Nº de jogadores", emoji="👥", style=discord.ButtonStyle.secondary)
    async def jogadores(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """A Steam não diz (ou diz errado) quantas pessoas jogam: qualquer um ensina o bot."""
        await interaction.response.send_modal(JogadoresModal(self.cog, self.appid, self.nome))


class OQueJogarView(discord.ui.View):
    """Menu de quantas pessoas + botões das outras sugestões (embaixo do /o-que-jogar)."""

    def __init__(self, cog: "JogarCog", preco_centavos: int) -> None:
        super().__init__(timeout=1800)
        self.cog, self.preco = cog, preco_centavos

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    @discord.ui.select(placeholder="👥 Para quantas pessoas?", row=0, options=[
        discord.SelectOption(label=f"{n} {'pessoa' if n == 1 else 'pessoas'}", value=str(n)) for n in range(1, 11)])
    async def pessoas(self, interaction: discord.Interaction, menu: discord.ui.Select) -> None:
        ids, origem = self.cog.quem_vai(interaction, None)
        await self.cog.resultado(interaction, int(menu.values[0]), ids, origem, self.preco)

    @discord.ui.button(label="Em alta", emoji="🔥", style=discord.ButtonStyle.secondary, row=1)
    async def em_alta(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.cog_ou_erro("RadarCog").mostrar_sugestoes(interaction, "em_alta")

    @discord.ui.button(label="Novidades", emoji="🆕", style=discord.ButtonStyle.secondary, row=1)
    async def novidades(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.cog_ou_erro("RadarCog").mostrar_sugestoes(interaction, "novidades")

    @discord.ui.button(label="Promoções", emoji="💸", style=discord.ButtonStyle.secondary, row=1)
    async def promocoes(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.cog_ou_erro("RadarCog").mostrar_sugestoes(interaction, "promocoes")

    @discord.ui.button(label="Grátis", emoji="🎁", style=discord.ButtonStyle.success, row=2)
    async def gratis(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.cog_ou_erro("GratisCog").mostrar(interaction)

    @discord.ui.button(label="Radar dos streamers", emoji="📡", style=discord.ButtonStyle.secondary, row=2)
    async def radar(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.cog_ou_erro("RadarCog").mostrar_radar(interaction)


class SteamModal(discord.ui.Modal, title="Vincular a sua Steam"):
    perfil = discord.ui.TextInput(label="Link do seu perfil na Steam",
                                  placeholder="https://steamcommunity.com/id/seunome", max_length=200)

    def __init__(self, cog: "JogarCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.cog_ou_erro("SteamCog").vincular(interaction, self.perfil.value.strip())


class PerfilView(discord.ui.View):
    """No seu próprio /perfil: vincular, trocar ou desvincular a Steam."""

    def __init__(self, cog: "JogarCog", dono: int, vinculada: bool) -> None:
        super().__init__(timeout=1800)
        self.cog, self.dono = cog, dono
        if vinculada:
            self.vincular.label, self.vincular.emoji = "Trocar conta da Steam", "🔄"
        else:
            self.remove_item(self.desvincular)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.dono:
            await interaction.response.send_message(
                "Esses botões são do dono do perfil. Use `/perfil` para ver o seu. 😉", ephemeral=True)
            return False
        return True

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    @discord.ui.button(label="Vincular Steam", emoji="🔗", style=discord.ButtonStyle.primary)
    async def vincular(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(SteamModal(self.cog))

    @discord.ui.button(label="Desvincular", emoji="🗑️", style=discord.ButtonStyle.secondary)
    async def desvincular(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.cog.cog_ou_erro("SteamCog").desvincular(interaction)


class JogarCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]

    async def cog_app_command_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    def cog_ou_erro(self, nome: str):
        cog = self.bot.get_cog(nome)
        if cog is None:
            raise ErroMaratona("Essa parte do bot não está ligada agora.")
        return cog

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
                          description="Sugestões de jogos: para quantas pessoas, em alta, grátis e radar dos streamers.")
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
        if preco_max is None:
            _, guardado = self.db.radar_config(interaction.guild_id)
            preco_centavos = PRECO_MAX_PADRAO if guardado is None else guardado
        else:
            preco_centavos = preco_max * 100  # o bot guarda preços em centavos
        if n == 0:
            # Sem número e sem call: mostra o "cardápio" (menu de pessoas + botões das sugestões).
            e = discord.Embed(title="🎮 O que jogar?", color=COR_DETALHE, description=(
                "Escolha **para quantas pessoas** no menu: o bot mostra os jogos que vocês já têm e que servem "
                "(do mais barato para o grupo) e novidades da loja.\n\n"
                "Ou veja as outras sugestões: **🔥 Em alta**, **🆕 Novidades** e **💸 Promoções** da Steam, "
                "**🎁 Grátis** para resgatar e o **📡 Radar dos streamers**.\n\n"
                "-# Dica: entrando numa call antes, o bot já sabe quem vai jogar."))
            e.set_thumbnail(url=url_nando(self.bot))
            await interaction.response.send_message(embed=e, view=OQueJogarView(self, preco_centavos))
            return
        await self.resultado(interaction, n, ids, origem, preco_centavos)

    async def resultado(self, interaction: discord.Interaction, n: int, ids: list[int], origem: str,
                        preco_centavos: int) -> None:
        """Os jogos para n pessoas (do comando ou do menu de pessoas)."""
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
                                     + ["Sabe quantos jogam? Ensine o bot pelo botão **👥** do `/jogo`."]),
                        inline=False)
        e.add_field(name="🆕 Novidades friendslop da loja que servem",
                    value=juntar(novidades, vazio="Nenhuma novidade da loja serve para esse número agora."),
                    inline=False)
        e.set_thumbnail(url=url_nando(self.bot))
        e.set_footer(text="O nº de jogadores vem da descrição da Steam. Errado ou faltando? Botão 👥 do /jogo")
        await interaction.followup.send(embed=e, view=OQueJogarView(self, preco_centavos), allowed_mentions=SEM_PING)

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
                return [], [], "Ninguém aqui tem a Steam vinculada (com jogos públicos). Vincule pelo `/perfil`."
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

    # ---- /jogo --------------------------------------------------------

    @app_commands.command(name="jogo", description="Ficha do jogo: preço, avaliações, nº de jogadores e quem do servidor tem.")
    @app_commands.describe(jogo="Nome do jogo (aparecem sugestões da Steam enquanto digita)")
    @app_commands.guild_only()
    async def cmd_jogo(self, interaction: discord.Interaction, jogo: str) -> None:
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
                    else "A Steam não diz (ensine pelo botão 👥 aqui embaixo)")
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
            linhas.append("Ninguém daqui tem a Steam vinculada (vincula pelo `/perfil`), então não sei quem tem.")
        querem = self.db.na_lista_de(guild_id, appid)
        if querem:
            linhas.append("📝 **Na lista de:** " + " ".join(f"<@{u}>" for u in querem))
        historico = ficha.nota_do_grupo(self.db.notas_do_servidor(guild_id), appid)
        if historico:
            media, quantas, vezes = historico
            nota = f" · ⭐ {estrelas(media)} {numero(media)} ({plural(quantas, 'nota', 'notas')})" if quantas else ""
            linhas.append(f"🎰 Saiu em {plural(vezes, 'maratona', 'maratonas')}{nota}")
        return juntar(linhas)

    # ---- /perfil -------------------------------------------------------

    @app_commands.command(name="perfil", description="Perfil gamer: horas, mais jogados e gêneros favoritos na Steam, "
                                                     "e os números nas maratonas.")
    @app_commands.describe(pessoa="De quem (vazio = você)")
    @app_commands.guild_only()
    async def cmd_perfil(self, interaction: discord.Interaction, pessoa: Optional[discord.Member] = None) -> None:
        alvo = pessoa or interaction.user
        if alvo.bot:
            raise ErroMaratona("Bots não têm perfil gamer. A gente só sorteia. 🤖")
        await interaction.response.defer(thinking=True)
        e = discord.Embed(title=f"🎮 Perfil de {alvo.display_name}", color=COR_PAINEL)
        e.set_thumbnail(url=alvo.display_avatar.url)
        await self.parte_steam(e, alvo)
        self.parte_servidor(e, interaction.guild_id, alvo.id)
        e.set_footer(text="Horas e jogos da Steam · o resto é das maratonas deste servidor")
        if alvo.id == interaction.user.id:  # no seu próprio perfil: botões da Steam
            vinculada = alvo.id in self.db.contas_steam([alvo.id])
            await interaction.followup.send(embed=e, view=PerfilView(self, alvo.id, vinculada),
                                            allowed_mentions=SEM_PING)
        else:
            await interaction.followup.send(embed=e, allowed_mentions=SEM_PING)

    async def parte_steam(self, e: discord.Embed, alvo: discord.abc.User) -> None:
        steam = self.bot.get_cog("SteamCog")
        conta = self.db.contas_steam([alvo.id]).get(alvo.id)
        if conta is None:
            e.description = "Sem Steam vinculada, então não sei das horas nem dos jogos. Clique em **🔗 Vincular Steam** aqui embaixo! 🎮"
            return
        if steam is not None and steam.api is not None:
            try:
                await steam.atualizar_bibliotecas([alvo.id])
                biblioteca = self.db.biblioteca_com_tempo(alvo.id)
                if biblioteca and not any(m for _, m, _ in biblioteca):
                    # biblioteca lida antes do bot guardar as horas: lê de novo uma vez
                    await steam.ler_biblioteca(alvo.id, conta["steamid"])
            except Exception as exc:
                log.info("Não consegui atualizar a biblioteca de %s: %s", alvo.id, exc)
            conta = self.db.contas_steam([alvo.id])[alvo.id]
        link = f"[Perfil na Steam](https://steamcommunity.com/profiles/{conta['steamid']})"
        if not conta["publico"]:
            e.description = f"{link} · os jogos dessa conta estão **privados**, então não dá para ver as horas."
            return
        biblioteca = self.db.biblioteca_com_tempo(alvo.id)
        total = sum(m for _, m, _ in biblioteca)
        recente = sum(m for _, _, m in biblioteca)
        abertos = sum(1 for _, m, _ in biblioteca if m)
        e.description = (f"{link} · **{perfil.horas(total)}** jogadas em {plural(len(biblioteca), 'jogo', 'jogos')} "
                         f"({abertos} já abertos)"
                         + (f"\n**{perfil.horas(recente)}** nas últimas 2 semanas" if recente else ""))

        top = perfil.mais_jogados(biblioteca, perfil.JOGOS_PARA_GENEROS)
        if steam is not None:
            try:
                await steam.analisar_na_loja([a for a, _ in top], perfil.JOGOS_PARA_GENEROS)  # gêneros e nomes
            except Exception as exc:
                log.info("Loja indisponível para o perfil: %s", exc)

        def nome(appid: int) -> str:
            return self.db.nome_conhecido(appid) or f"Jogo {appid}"

        if top:
            e.add_field(name="🏆 Mais jogados", value="\n".join(
                f"**{nome(a)}** — {perfil.horas(m)}" for a, m in top[:5]))
        recentes = perfil.mais_jogados(biblioteca, 3, recentes=True)
        if recentes:
            e.add_field(name="🔥 Jogando ultimamente", value="\n".join(
                f"**{nome(a)}** — {perfil.horas(m)}" for a, m in recentes))
        generos = perfil.generos_favoritos(biblioteca, self.db.generos_de([a for a, _ in top]))
        if generos:
            e.add_field(name="🏷️ Gêneros favoritos (pelo tempo jogado)",
                        value=" · ".join(f"**{g}** {pct}%" for g, pct in generos), inline=False)

    def parte_servidor(self, e: discord.Embed, guild_id: int, user_id: int) -> None:
        p = self.db.perfil_no_servidor(guild_id, user_id)
        linhas = []
        if p["maratonas"]:
            linhas.append(f"🎰 {plural(p['maratonas'], 'maratona', 'maratonas')} · colocou "
                          f"{plural(p['colocou'], 'jogo', 'jogos')} na roleta ({p['sairam']} "
                          f"{'saiu' if p['sairam'] == 1 else 'saíram'})")
        else:
            linhas.append("🎰 Ainda não jogou nenhuma maratona aqui.")
        if p["notas"]:
            favorito = f" · favorito: **{p['favorito']}**" if p["favorito"] else ""
            linhas.append(f"⭐ Deu {plural(p['notas'], 'nota', 'notas')}, média {numero(p['media_notas'])}{favorito}")
        if p["reclamacoes"]:
            linhas.append(f"😤 {plural(p['reclamacoes'], 'reclamação', 'reclamações')} no Mural da Vergonha")
        else:
            linhas.append("😇 Nunca foi pro Mural da Vergonha")
        querem = []
        if p["lista"]:
            querem.append(f"{plural(p['lista'], 'jogo', 'jogos')} na /lista")
        if p["desejos_steam"]:
            querem.append(f"{p['desejos_steam']} na lista de desejos da Steam")
        if querem:
            linhas.append("📝 " + " · ".join(querem))
        e.add_field(name="🏠 Nas maratonas daqui", value="\n".join(linhas), inline=False)

    @cmd_jogo.autocomplete("jogo")
    async def _autocompletar(self, interaction: discord.Interaction, atual: str):
        return await autocompletar_jogo(self.bot, interaction, atual)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(JogarCog(bot))
