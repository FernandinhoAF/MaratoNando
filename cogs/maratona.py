"""Comandos, painel e roleta da maratona de jogos."""

import asyncio
import logging
import random
from datetime import datetime
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from biblioteca import resumo_grupo
from nomes import ler_escolha, valor_escolha
from db import Database, ErroMaratona, ItemLista, Jogo, Maratona, limpar_nome, separar_nomes

log = logging.getLogger("maratonando")

COR_PAINEL = discord.Color.from_rgb(124, 77, 255)   # roxo
COR_ROLETA = discord.Color.from_rgb(255, 196, 0)    # amarelo
COR_FIM = discord.Color.from_rgb(46, 204, 113)      # verde
COR_VERGONHA = discord.Color.from_rgb(231, 76, 60)  # vermelho

# Pausas (em segundos) entre os quadros da roleta: vai desacelerando, como uma roleta de verdade.
TEMPOS_ROLETA = (0.6, 0.8, 1.1, 1.4)
SUSPENSE_FINAL = 1.6

MEDALHAS = ("🥇", "🥈", "🥉")
SEM_PING = discord.AllowedMentions.none()  # menciona o nome sem notificar ninguém


# ---------------------------------------------------------------- utilidades

def plural(n: int, singular: str, plural_: str) -> str:
    return f"{n} {singular if n == 1 else plural_}"


def carimbo(iso: str, estilo: str = "R") -> str:
    """Data no formato do Discord, que aparece no fuso de quem lê (ex.: "há 2 horas")."""
    return f"<t:{int(datetime.fromisoformat(iso).timestamp())}:{estilo}>"


def duracao(inicio_iso: str, fim_iso: str) -> str:
    minutos = int((datetime.fromisoformat(fim_iso) - datetime.fromisoformat(inicio_iso)).total_seconds() // 60)
    horas, minutos = divmod(minutos, 60)
    if horas == 0:
        return plural(minutos, "minuto", "minutos")
    return f"{horas}h{minutos:02d}"


def juntar(linhas: list[str], vazio: str = "—", limite: int = 1024) -> str:
    """Junta linhas sem passar do limite de caracteres de um campo de embed."""
    if not linhas:
        return vazio
    saida: list[str] = []
    tamanho = 0
    for i, linha in enumerate(linhas):
        aviso = f"…e mais {len(linhas) - i}"
        if tamanho + len(linha) + 1 + len(aviso) > limite:
            saida.append(aviso)
            break
        saida.append(linha)
        tamanho += len(linha) + 1
    return "\n".join(saida)


def ranking(mural: list[tuple[int, int]]) -> list[str]:
    linhas = []
    for i, (user_id, n) in enumerate(mural):
        marca = MEDALHAS[i] if i < len(MEDALHAS) else f"`{i + 1}.`"
        linhas.append(f"{marca} <@{user_id}> — {plural(n, 'reclamação', 'reclamações')}")
    return linhas


def texto_resultado(adicionados: list[str], recusados: list[tuple[str, str]], titulo_ok: str) -> str:
    """Mensagem de "isso entrou / isso ficou de fora" usada ao adicionar jogos."""
    partes = []
    if adicionados:
        partes.append(f"✅ **{titulo_ok}:**\n" + "\n".join(f"• {n}" for n in adicionados))
    if recusados:
        partes.append("⚠️ **Ficaram de fora:**\n" + "\n".join(f"• {n} — {motivo}" for n, motivo in recusados))
    if not partes:
        partes.append("Não encontrei nenhum nome de jogo aí. 🤔")
    return "\n\n".join(partes)


async def autocompletar_jogo(bot: commands.Bot, interaction: discord.Interaction,
                            atual: str) -> list[app_commands.Choice[str]]:
    """Sugestões enquanto a pessoa digita o nome de um jogo.

    📝 jogos da lista pessoal dela · 🎮 jogos da Steam · ✍️ usar exatamente o que digitou
    (para jogos de fora da Steam). Escolhendo um 🎮, o bot já sabe qual jogo da Steam é.
    """
    atual = atual.strip()
    opcoes: list[app_commands.Choice[str]] = []
    db: Database = bot.db  # type: ignore[attr-defined]
    if atual:
        for item in db.lista(interaction.user.id):
            if atual.casefold() in item.nome.casefold():
                opcoes.append(app_commands.Choice(name=f"📝 {item.nome}"[:100], value=item.nome[:100]))
    steam = bot.get_cog("SteamCog")
    if steam is not None and len(atual) >= 2:
        try:
            for appid, nome in await steam.sugestoes(atual):
                opcoes.append(app_commands.Choice(name=f"🎮 {nome}"[:100], value=valor_escolha(appid, nome)))
        except Exception:
            pass  # Steam lenta ou fora do ar: mostra só o resto
    if atual:
        opcoes.append(app_commands.Choice(name=f"✍️ Usar como escrevi: {atual}"[:100],
                                          value=valor_escolha(0, atual)))
    vistos: set[str] = set()
    unicas = [o for o in opcoes if not (o.value in vistos or vistos.add(o.value))]
    return unicas[:25]


def mencao_do_cargo(db: Database, guild_id: int) -> tuple[str, discord.AllowedMentions, str]:
    """Prepara a marcação do cargo de jogos (/config cargo-jogos).

    Devolve (texto da menção, permissão de notificação, aviso para quem chamou).
    O cargo só é marcado de verdade se já passou o tempo mínimo desde a última vez;
    senão, o texto vem vazio e o aviso explica.
    """
    cargo = db.cargo_jogos(guild_id)
    if cargo is None:
        return "", SEM_PING, ""
    if not db.tentar_marcar_cargo(guild_id):
        return "", SEM_PING, " (o cargo foi marcado há pouco, então dessa vez não marquei de novo)"
    return f"<@&{cargo}> ", discord.AllowedMentions(everyone=False, users=False,
                                                    roles=[discord.Object(id=cargo)]), ""


async def responder_privado(interaction: discord.Interaction, texto: str) -> None:
    """Resposta que só quem clicou/usou o comando vê."""
    if interaction.response.is_done():
        await interaction.followup.send(texto, ephemeral=True)
    else:
        await interaction.response.send_message(texto, ephemeral=True)


async def tratar_erro(interaction: discord.Interaction, erro: Exception) -> None:
    original = getattr(erro, "original", erro)
    if isinstance(original, ErroMaratona):
        texto = str(original)
    else:
        log.exception("Erro inesperado", exc_info=original)
        texto = "Deu ruim aqui 😵 Tenta de novo daqui a pouco."
    try:
        await responder_privado(interaction, texto)
    except discord.HTTPException:
        pass


# ---------------------------------------------------------------- views

class PainelView(discord.ui.View):
    """Botões do painel. É "persistente": continua funcionando depois que o bot reinicia.

    Para isso, cada botão tem um custom_id fixo e a view é registrada com
    bot.add_view() quando o bot liga (veja Maratona.cog_load).
    """

    def __init__(self, cog: "MaratonaCog") -> None:
        super().__init__(timeout=None)
        self.cog = cog

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    async def _maratona(self, interaction: discord.Interaction) -> Optional[Maratona]:
        m = self.cog.db.maratona_por_painel(interaction.message.id)
        if m is None or not m.aberta:
            await responder_privado(interaction, "Essa maratona já terminou. 🏁")
            return None
        return m

    @discord.ui.button(label="Adicionar jogos", emoji="➕", style=discord.ButtonStyle.primary,
                       custom_id="maratonando:adicionar")
    async def adicionar(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if m := await self._maratona(interaction):
            await self.cog.abrir_adicionar(interaction, m)

    @discord.ui.button(label="Meus jogos", emoji="📋", style=discord.ButtonStyle.secondary,
                       custom_id="maratonando:meus")
    async def meus(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if m := await self._maratona(interaction):
            await self.cog.mostrar_meus_jogos(interaction, m)

    @discord.ui.button(label="Sortear", emoji="🎲", style=discord.ButtonStyle.success,
                       custom_id="maratonando:sortear")
    async def sortear(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if m := await self._maratona(interaction):
            await self.cog.sortear(interaction, m)

    @discord.ui.button(label="Tô jogando", emoji="🙋", style=discord.ButtonStyle.secondary,
                       custom_id="maratonando:jogando")
    async def jogando(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        """Para quem chegou no meio e vai jogar, mesmo sem colocar jogo na roleta."""
        if m := await self._maratona(interaction):
            await self.cog.alternar_jogando(interaction, m)

    @discord.ui.button(label="Encerrar", emoji="🏁", style=discord.ButtonStyle.danger,
                       custom_id="maratonando:encerrar")
    async def encerrar(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if m := await self._maratona(interaction):
            await self.cog.pedir_encerramento(interaction, m)


class AdicionarJogosModal(discord.ui.Modal, title="Colocar jogos na roleta"):
    jogos = discord.ui.TextInput(
        label="Seus jogos (um por linha)",
        style=discord.TextStyle.paragraph,
        placeholder="Hollow Knight\nStardew Valley\nPapers, Please",
        max_length=1500,
    )

    def __init__(self, cog: "MaratonaCog", maratona: Maratona) -> None:
        super().__init__()
        self.cog = cog
        self.maratona = maratona
        if maratona.limite:
            self.jogos.label = f"Seus jogos (um por linha, até {maratona.limite})"

    async def on_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.receber_nomes(interaction, separar_nomes(self.jogos.value),
                                     self.cog.concluidor_roleta(self.maratona.id))


class DuvidasView(discord.ui.View):
    """ "Você quis dizer…?": um menu para cada nome que não bateu com nenhum jogo da Steam."""

    def __init__(self, user_id: int, itens: list[tuple[str, Optional[int]]],
                 duvidas: list[tuple[str, list[tuple[int, str]]]], concluir) -> None:
        super().__init__(timeout=600)
        self.user_id = user_id
        self.itens = itens          # o que já está resolvido (na ordem em que foi digitado)
        self.duvidas = duvidas[:4]  # cabem 4 menus + a linha do botão
        self.sobras = duvidas[4:]   # se tiver mais que 4 dúvidas, essas entram como foram escritas
        self.concluir = concluir
        self.menus: list[discord.ui.Select] = []
        for linha, (digitado, sugestoes) in enumerate(self.duvidas):
            opcoes = [discord.SelectOption(label=nome[:100], value=valor_escolha(appid, nome), emoji="🎮",
                                           default=(i == 0)) for i, (appid, nome) in enumerate(sugestoes)]
            opcoes.append(discord.SelectOption(label=f"Manter como escrevi: {digitado}"[:100],
                                               value=valor_escolha(0, digitado), emoji="✍️"))
            menu = discord.ui.Select(placeholder=f"“{digitado}”: você quis dizer…?"[:150], options=opcoes, row=linha)
            menu.callback = self._marcou
            self.menus.append(menu)
            self.add_item(menu)
        botao = discord.ui.Button(label="Confirmar", emoji="✅", style=discord.ButtonStyle.success, row=4)
        botao.callback = self._confirmar
        self.add_item(botao)

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    async def _marcou(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()  # só guarda a escolha; vale quando clicar em Confirmar

    def texto(self) -> str:
        linhas = [f"• “{d}”" for d, _ in self.duvidas]
        texto = ("🔎 Não achei estes jogos do jeito que foram escritos. Confere se é algum destes "
                 "(ou escolha **Manter como escrevi**) e clique em **Confirmar**:\n" + "\n".join(linhas))
        if self.sobras:
            texto += f"\n-# Mais {len(self.sobras)} nome(s) vão entrar do jeito que foram escritos."
        return texto

    async def _confirmar(self, interaction: discord.Interaction) -> None:
        escolhidos: dict[str, tuple[str, Optional[int]]] = {}
        for (digitado, _), menu in zip(self.duvidas, self.menus):
            valor = menu.values[0] if menu.values else next(o.value for o in menu.options if o.default)
            nome, appid = ler_escolha(valor)
            escolhidos[digitado] = (nome, appid or None)
        itens = [escolhidos.get(nome, (nome, appid)) for nome, appid in self.itens]
        self.stop()
        await self.concluir(interaction, itens, True)


class EscolherDaListaView(discord.ui.View):
    """Aparece ao clicar em "Adicionar jogos" quando a pessoa tem jogos salvos na lista pessoal.

    Ela marca os jogos da lista que quer colocar na roleta, ou clica em
    "Escrever outros jogos" para abrir o formulário normal.
    """

    def __init__(self, cog: "MaratonaCog", maratona: Maratona, user_id: int) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.maratona = maratona
        self.user_id = user_id
        self.montar()

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    def itens(self) -> list[ItemLista]:
        return self.cog.db.lista_fora_da_maratona(self.user_id, self.maratona.id)

    def montar(self) -> None:
        self.clear_items()
        itens = self.itens()[:25]  # o Discord aceita no máximo 25 opções num menu
        if itens:
            menu = discord.ui.Select(
                placeholder="Marque os jogos da sua lista…",
                min_values=1, max_values=len(itens),
                options=[discord.SelectOption(label=i.nome[:100], value=str(i.id), emoji="🎲") for i in itens],
            )
            menu.callback = self.escolher
            self.add_item(menu)
        botao = discord.ui.Button(label="Escrever outros jogos", emoji="✍️", style=discord.ButtonStyle.secondary)
        botao.callback = self.escrever
        self.add_item(botao)

    def texto_inicial(self) -> str:
        n = len(self.itens())
        return (f"📝 Você tem **{plural(n, 'jogo salvo', 'jogos salvos')}** na sua lista. "
                "Marque quais quer colocar na roleta, ou escreva outros.")

    async def escolher(self, interaction: discord.Interaction) -> None:
        menu: discord.ui.Select = self.children[0]  # type: ignore[assignment]
        escolhidos = {int(v) for v in menu.values}
        nomes = [i.nome for i in self.itens() if i.id in escolhidos]
        adicionados, recusados = self.cog.db.adicionar_jogos(self.maratona.id, self.user_id, nomes)
        self.montar()
        texto = texto_resultado(adicionados, recusados, "Na roleta")
        texto += "\n\n-# Os jogos continuam na sua lista até serem sorteados. Ninguém vê quais você colocou. 🤫"
        await interaction.response.edit_message(content=texto, view=self)
        if adicionados:
            await self.cog.atualizar_painel(self.maratona.id)
            self.cog.em_segundo_plano(
                self.cog.completar_com_custo(interaction, self.maratona.id, texto, adicionados, view=self))

    async def escrever(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(AdicionarJogosModal(self.cog, self.maratona))


class SalvarNaListaModal(discord.ui.Modal, title="Salvar jogos na sua lista"):
    jogos = discord.ui.TextInput(
        label="Jogos (um por linha)",
        style=discord.TextStyle.paragraph,
        placeholder="Aquele jogo que você lembrou no meio da semana…",
        max_length=1500,
    )

    def __init__(self, cog: "MaratonaCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.receber_nomes(interaction, separar_nomes(self.jogos.value), self.cog.concluir_lista)


class MinhaListaView(discord.ui.View):
    """Lista pessoal (privada), com um menu para apagar jogos dela."""

    def __init__(self, cog: "MaratonaCog", user_id: int) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.user_id = user_id
        self.montar()

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    def montar(self) -> None:
        self.clear_items()
        itens = self.cog.db.lista(self.user_id)[:25]
        if itens:
            menu = discord.ui.Select(
                placeholder="Apagar um jogo da lista…",
                options=[discord.SelectOption(label=i.nome[:100], value=str(i.id), emoji="🗑️") for i in itens],
            )
            menu.callback = self.remover
            self.add_item(menu)
        if self.nao_ligados():
            botao = discord.ui.Button(label="Conferir nomes na Steam", emoji="🔎", style=discord.ButtonStyle.primary)
            botao.callback = self.conferir
            self.add_item(botao)

    def nao_ligados(self) -> list[str]:
        """Jogos da lista que o bot ainda não sabe qual jogo da Steam são."""
        steam = self.cog.db.status_steam_da_lista(self.user_id)
        return [i.nome for i in self.cog.db.lista(self.user_id) if not steam.get(i.chave)]

    async def conferir(self, interaction: discord.Interaction) -> None:
        await self.cog.conferir_lista(interaction, self.nao_ligados())

    def embed(self) -> discord.Embed:
        itens = self.cog.db.lista(self.user_id)
        promocoes = self.cog.db.promocoes_da_lista(self.user_id)
        steam = self.cog.db.status_steam_da_lista(self.user_id)
        linhas = []
        for i in itens:
            linha = f"• {i.nome}"
            if i.chave in promocoes:
                p = promocoes[i.chave]
                linha += f" · 💸 **−{p['desconto']}%** ({p['preco_texto']})"
            elif i.chave in steam and steam[i.chave] is None:
                linha += " · _(não achei na Steam, confira o nome)_"
            linhas.append(linha)
        e = discord.Embed(
            title="📝 Sua lista de jogos", color=COR_PAINEL,
            description=juntar(linhas, limite=4000,
                               vazio="Sua lista está vazia. Use `/lista adicionar` para guardar jogos."),
        )
        e.set_footer(text="Só você vê esta lista. Na maratona, ela aparece quando você clica em Adicionar jogos.")
        return e

    async def remover(self, interaction: discord.Interaction) -> None:
        menu: discord.ui.Select = self.children[0]  # type: ignore[assignment]
        item = self.cog.db.remover_da_lista(self.user_id, int(menu.values[0]))
        self.montar()
        await interaction.response.edit_message(content=f"🗑️ **{item.nome}** saiu da sua lista.",
                                                embed=self.embed(), view=self)


class MeusJogosView(discord.ui.View):
    """Lista privada dos jogos de uma pessoa, com um menu para tirar jogos da roleta."""

    def __init__(self, cog: "MaratonaCog", maratona: Maratona, user_id: int) -> None:
        super().__init__(timeout=300)
        self.cog = cog
        self.maratona = maratona
        self.user_id = user_id
        self.montar()

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    def montar(self) -> None:
        self.clear_items()
        pendentes = [j for j in self.cog.db.jogos_do_usuario(self.maratona.id, self.user_id) if not j.jogado]
        if pendentes:
            menu = discord.ui.Select(
                placeholder="Tirar um jogo da roleta…",
                options=[discord.SelectOption(label=j.nome[:100], value=str(j.id), emoji="🗑️")
                         for j in pendentes[:25]],
            )
            menu.callback = self.remover
            self.add_item(menu)

    def embed(self) -> discord.Embed:
        meus = self.cog.db.jogos_do_usuario(self.maratona.id, self.user_id)
        linhas = [f"✅ ~~{j.nome}~~ (saiu como #{j.ordem})" if j.jogado else f"🎲 {j.nome}" for j in meus]
        e = discord.Embed(title="📋 Seus jogos nesta maratona", color=COR_PAINEL,
                          description=juntar(linhas, vazio="Você ainda não colocou nenhum jogo.", limite=4000))
        if self.maratona.limite:
            e.set_footer(text=f"Limite: {self.maratona.limite} jogos por pessoa")
        return e

    async def remover(self, interaction: discord.Interaction) -> None:
        menu: discord.ui.Select = self.children[0]  # type: ignore[assignment]
        jogo = self.cog.db.remover_jogo(self.maratona.id, self.user_id, int(menu.values[0]))
        self.montar()
        await interaction.response.edit_message(content=f"🗑️ **{jogo.nome}** saiu da roleta.",
                                                embed=self.embed(), view=self)
        await self.cog.atualizar_painel(self.maratona.id)


class ConfirmarEncerramentoView(discord.ui.View):
    def __init__(self, cog: "MaratonaCog", maratona: Maratona) -> None:
        super().__init__(timeout=60)
        self.cog = cog
        self.maratona = maratona

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    @discord.ui.button(label="Sim, encerrar", emoji="🏁", style=discord.ButtonStyle.danger)
    async def confirmar(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.stop()
        await self.cog.encerrar(interaction, self.maratona)

    @discord.ui.button(label="Cancelar", style=discord.ButtonStyle.secondary)
    async def cancelar(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.edit_message(content="Beleza, a maratona continua! 🎮", view=None)


# ---------------------------------------------------------------- cog

class MaratonaCog(commands.Cog):
    maratona = app_commands.Group(
        name="maratona",
        description="Organize uma maratona de jogos com roleta.",
        guild_only=True,
    )

    lista = app_commands.Group(
        name="lista",
        description="Sua lista pessoal de jogos para as próximas maratonas.",
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]
        self.painel_view = PainelView(self)
        self.girando: set[int] = set()  # maratonas com a roleta girando agora
        self.tarefas: set[asyncio.Task] = set()
        # Menu de botão direito: clique numa pessoa → Apps → "Reclamou!"
        self.menu_reclamou = app_commands.ContextMenu(
            name="Reclamou!", callback=self._menu_reclamou,
            allowed_contexts=app_commands.AppCommandContext(guild=True),
        )

    async def cog_load(self) -> None:
        self.bot.add_view(self.painel_view)  # religa os botões dos painéis antigos
        self.bot.tree.add_command(self.menu_reclamou)

    async def cog_unload(self) -> None:
        self.bot.tree.remove_command(self.menu_reclamou.name, type=self.menu_reclamou.type)

    async def cog_app_command_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    # ---- permissões ------------------------------------------------

    @staticmethod
    def pode_organizar(interaction: discord.Interaction, m: Maratona) -> bool:
        """Quem abriu a maratona, ou quem tem "Gerenciar servidor"."""
        if interaction.user.id == m.host_id:
            return True
        perms = getattr(interaction.user, "guild_permissions", None)
        return bool(perms and perms.manage_guild)

    def exigir_aberta(self, interaction: discord.Interaction) -> Maratona:
        m = self.db.maratona_aberta(interaction.guild_id)
        if m is None:
            raise ErroMaratona("Não tem nenhuma maratona aberta agora. Use `/maratona criar` para começar uma!")
        return m

    # ---- painel ----------------------------------------------------

    def embed_painel(self, m: Maratona) -> discord.Embed:
        participantes = self.db.participantes(m.id)
        sem_jogo = self.db.extras(m.id)
        restantes, jogados = self.db.contagem(m.id)
        atual = self.db.ultimo_sorteado(m.id)

        if m.aberta:
            descricao = (
                "Cada um coloca seus jogos na roleta (ninguém vê os dos outros). "
                "O bot sorteia, e o jogo que cair é o que a gente joga.\n"
                "**Regra de ouro:** caiu, jogou. Sem reclamar. 🫡\n\n"
                f"Organização: <@{m.host_id}> · aberta {carimbo(m.criada_em)}"
            )
            if m.limite:
                descricao += f"\nLimite: **{m.limite}** jogos por pessoa"
        else:
            descricao = f"🏁 **Maratona encerrada** {carimbo(m.encerrada_em)}. Valeu, galera!"

        e = discord.Embed(title=f"🎮 {m.nome}", description=descricao,
                          color=COR_PAINEL if m.aberta else discord.Color.dark_grey())
        linhas = [f"<@{p.user_id}> — {plural(p.total, 'jogo', 'jogos')}" for p in participantes]
        linhas += [f"<@{uid}> — entrou sem jogo" for uid in sem_jogo]
        e.add_field(name=f"👥 Jogando ({len(linhas)})",
                    value=juntar(linhas, vazio="Ninguém ainda. Clique em **Adicionar jogos**!"), inline=False)
        e.add_field(name="🎰 Roleta",
                    value=f"{plural(restantes, 'jogo restante', 'jogos restantes')}\n"
                          f"{plural(jogados, 'jogo jogado', 'jogos jogados')}")
        e.add_field(name="🕹️ Jogando agora" if m.aberta else "🕹️ Último jogo",
                    value=f"**{atual.nome}**\nde <@{atual.user_id}>" if atual else "Nada sorteado ainda")
        mural = self.db.mural(m.id)
        if mural:
            e.add_field(name="😤 Mural da Vergonha", value=juntar(ranking(mural[:3])), inline=False)
        return e

    async def atualizar_painel(self, maratona_id: int) -> None:
        m = self.db.maratona(maratona_id)
        if m is None or m.panel_message_id is None:
            return
        canal = self.bot.get_channel(m.channel_id)
        if canal is None:
            return
        try:
            await canal.get_partial_message(m.panel_message_id).edit(
                embed=self.embed_painel(m), view=self.painel_view if m.aberta else None
            )
        except discord.HTTPException as exc:
            log.warning("Não consegui atualizar o painel da maratona %s: %s", maratona_id, exc)

    async def enviar_painel(self, interaction: discord.Interaction, m: Maratona, texto: Optional[str] = None,
                            mencoes: discord.AllowedMentions = SEM_PING) -> None:
        await interaction.response.send_message(texto, embed=self.embed_painel(m), view=self.painel_view,
                                                allowed_mentions=mencoes)
        msg = await interaction.original_response()
        self.db.definir_painel(m.id, msg.channel.id, msg.id)

    async def abrir_adicionar(self, interaction: discord.Interaction, m: Maratona) -> None:
        """Botão "Adicionar jogos": mostra a lista salva (se tiver) ou abre direto o formulário."""
        view = EscolherDaListaView(self, m, interaction.user.id)
        if not view.itens():
            await interaction.response.send_modal(AdicionarJogosModal(self, m))
            return
        await interaction.response.send_message(view.texto_inicial(), view=view, ephemeral=True)

    def em_segundo_plano(self, coro) -> None:
        tarefa = asyncio.create_task(coro)
        self.tarefas.add(tarefa)
        tarefa.add_done_callback(self.tarefas.discard)

    async def quem_tem(self, maratona_id: int, nome: str, appid: Optional[int] = None):
        steam = self.bot.get_cog("SteamCog")
        return await steam.quem_tem(maratona_id, nome, appid) if steam else None

    # ---- nomes digitados de qualquer jeito -------------------------

    async def resolver_nomes(self, nomes: list[str]):
        """Para cada nome: o jogo da Steam certinho, uma lista de parecidos (dúvida) ou nada.

        Devolve ({nome: (appid, nome oficial) ou None}, [(nome, [parecidos])]).
        Se a Steam estiver fora do ar, tudo entra do jeito que foi escrito.
        """
        steam = self.bot.get_cog("SteamCog")
        achados: dict[str, Optional[tuple[int, str]]] = {}
        duvidas: list[tuple[str, list[tuple[int, str]]]] = []
        for nome in nomes:
            parecidos: list[tuple[int, str]] = []
            try:
                achados[nome] = await steam.reconhecer(nome) if steam else None
                if achados[nome] is None and steam:
                    parecidos = await steam.parecidos(nome)
            except Exception as exc:
                log.warning("Não consegui conferir %r na Steam: %s", nome, exc)
                achados[nome] = None
            if parecidos:
                duvidas.append((nome, parecidos))
        return achados, duvidas

    async def receber_nomes(self, interaction: discord.Interaction, nomes: list[str], concluir) -> None:
        """Caminho único para jogos digitados (formulário, /maratona adicionar, /lista adicionar).

        1. O que veio escolhido do autocompletar já está certo.
        2. O resto é conferido na Steam: nome exato vira o nome oficial; nome parecido
           vira a pergunta "você quis dizer…?"; nada parecido entra como foi escrito.
        3. concluir(interaction, [(nome, appid)], via_botao) coloca na roleta/lista e responde.
        """
        escolhidos = [(limpar_nome(nome), appid) for nome, appid in (ler_escolha(n) for n in nomes if n.strip())]
        escolhidos = [(nome, appid) for nome, appid in escolhidos if nome]
        pendentes = [nome for nome, appid in escolhidos if appid is None]
        steam = self.bot.get_cog("SteamCog")
        achados, duvidas = {}, []
        if pendentes and steam is not None:
            if not interaction.response.is_done():
                await interaction.response.defer(ephemeral=True, thinking=True)
            achados, duvidas = await self.resolver_nomes(pendentes)

        itens: list[tuple[str, Optional[int]]] = []
        for nome, appid in escolhidos:
            if appid:  # escolhido no autocompletar (🎮)
                if steam is not None:
                    steam.lembrar_nome(nome, appid, nome)
                itens.append((nome, appid))
            elif appid == 0 or not achados.get(nome):  # ✍️ "usar como escrevi", ou não achado
                itens.append((nome, None))
            else:  # nome exato na Steam: troca pelo nome oficial
                itens.append((achados[nome][1], achados[nome][0]))

        if duvidas:
            view = DuvidasView(interaction.user.id, itens, duvidas, concluir)
            await interaction.followup.send(view.texto(), view=view, ephemeral=True)
        else:
            await concluir(interaction, itens, False)

    @staticmethod
    async def _responder(interaction: discord.Interaction, texto: str, via_botao: bool) -> None:
        if via_botao:
            await interaction.response.edit_message(content=texto, view=None)
        elif interaction.response.is_done():
            await interaction.followup.send(texto, ephemeral=True)
        else:
            await interaction.response.send_message(texto, ephemeral=True)

    def concluidor_roleta(self, maratona_id: int):
        async def concluir(interaction: discord.Interaction, itens, via_botao: bool) -> None:
            nomes = [n for n, _ in itens if n]
            adicionados, recusados = self.db.adicionar_jogos(maratona_id, interaction.user.id, nomes,
                                                             {n: a for n, a in itens})
            texto = texto_resultado(adicionados, recusados, "Na roleta")
            texto += "\n\n-# Só você vê esta mensagem. Ninguém sabe quais jogos você colocou. 🤫"
            await self._responder(interaction, texto, via_botao)
            if adicionados:
                await self.atualizar_painel(maratona_id)
                self.em_segundo_plano(self.completar_com_custo(interaction, maratona_id, texto, adicionados))
        return concluir

    async def conferir_lista(self, interaction: discord.Interaction, nomes: list[str]) -> None:
        """Botão "Conferir nomes na Steam" do /lista ver: liga os jogos da lista à Steam, corrigindo os nomes."""
        if not nomes:
            await responder_privado(interaction, "Todos os jogos da sua lista já estão ligados à Steam. 👍")
            return
        originais = list(nomes)

        async def concluir(inter: discord.Interaction, itens, via_botao: bool) -> None:
            mudancas, ligados = [], 0
            for antigo, (novo, appid) in zip(originais, itens):
                if appid:
                    self.db.renomear_na_lista(inter.user.id, antigo, novo)
                    self.db.salvar_steam_nome(novo, appid)
                    ligados += 1
                    if novo != antigo:
                        mudancas.append(f"• {antigo} → **{novo}**")
            texto = f"🔎 Liguei **{plural(ligados, 'jogo', 'jogos')}** da sua lista à Steam."
            if mudancas:
                texto += "\n" + "\n".join(mudancas)
            sobrou = len(originais) - ligados
            if sobrou:
                texto += f"\n-# {plural(sobrou, 'jogo ficou', 'jogos ficaram')} como estava (não achei na Steam ou você quis manter)."
            await self._responder(inter, texto, via_botao)

        await self.receber_nomes(interaction, nomes, concluir)

    async def concluir_lista(self, interaction: discord.Interaction, itens, via_botao: bool) -> None:
        salvos, recusados = self.db.salvar_na_lista(interaction.user.id, [n for n, _ in itens if n])
        for nome, appid in itens:
            if appid and nome in salvos:
                self.db.salvar_steam_nome(nome, appid)
        texto = texto_resultado(salvos, recusados, "Salvo na sua lista")
        if salvos:
            texto += ("\n\n-# Na próxima maratona, quando você clicar em **Adicionar jogos**, "
                      "sua lista vai aparecer. Só você vê. 🤫")
        await self._responder(interaction, texto, via_botao)

    async def completar_com_custo(self, interaction: discord.Interaction, maratona_id: int, texto: str,
                                  nomes: list[str], view: Optional[discord.ui.View] = None) -> None:
        """Depois de confirmar os jogos colocados na roleta, acrescenta quanto cada um custa para o grupo."""
        linhas = []
        for nome in nomes:
            info = await self.quem_tem(maratona_id, nome)
            if info is not None:
                opcao, _ = info
                linhas.append(f"• **{nome}**: {resumo_grupo(opcao, mencoes=False)}")
        if not linhas:
            return
        texto += "\n\n💰 **Para o grupo** (pensem nos amigos 😉):\n" + "\n".join(linhas)
        try:
            if view is not None:
                await interaction.edit_original_response(content=texto[:2000], view=view)
            else:
                await interaction.edit_original_response(content=texto[:2000])
        except discord.HTTPException:
            pass

    async def alternar_jogando(self, interaction: discord.Interaction, m: Maratona) -> None:
        uid = interaction.user.id
        if uid in self.db.extras(m.id):
            self.db.sair_sem_jogo(m.id, uid)
            texto = "👋 Você saiu da lista de quem está jogando."
        elif self.db.entrar_sem_jogo(m.id, uid):
            texto = ("🙋 Você entrou na maratona! Já conta nos times e no `/maratona baratos`. "
                     "Se quiser colocar jogo na roleta, é só clicar em **Adicionar jogos**.")
        else:
            texto = "Você já está jogando (colocou jogos na roleta). 👍"
        await interaction.response.send_message(texto, ephemeral=True)
        await self.atualizar_painel(m.id)

    async def mostrar_meus_jogos(self, interaction: discord.Interaction, m: Maratona) -> None:
        view = MeusJogosView(self, m, interaction.user.id)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    # ---- /maratona -------------------------------------------------

    @maratona.command(name="criar", description="Abre uma nova maratona de jogos neste canal.")
    @app_commands.describe(nome="Nome da maratona (opcional)",
                           limite="Máximo de jogos por pessoa (opcional; sem limite se ficar vazio)",
                           avisar="Marcar o cargo de jogos? (padrão: sim, se o cargo estiver configurado)")
    async def cmd_criar(self, interaction: discord.Interaction,
                        nome: Optional[app_commands.Range[str, 1, 60]] = None,
                        limite: Optional[app_commands.Range[int, 1, 25]] = None,
                        avisar: bool = True) -> None:
        aberta = self.db.maratona_aberta(interaction.guild_id)
        if aberta:
            raise ErroMaratona(f"Já tem uma maratona aberta em <#{aberta.channel_id}>. "
                               "Encerre ela antes (ou use `/maratona painel` para achar o painel).")
        m = self.db.criar_maratona(interaction.guild_id, interaction.channel_id, interaction.user.id,
                                   nome or "Maratona de Jogos", limite)
        if avisar:
            mencao, permissao, aviso = mencao_do_cargo(self.db, interaction.guild_id)
        else:
            mencao, permissao, aviso = "", SEM_PING, ""
        texto = None
        if mencao:
            texto = (f"{mencao}🎮 **Maratona aberta!** Coloquem seus jogos na roleta (**➕ Adicionar jogos**) "
                     "ou cliquem em **🙋 Tô jogando**.")
        await self.enviar_painel(interaction, m, texto, permissao)
        if aviso:
            await interaction.followup.send(f"A maratona foi aberta{aviso}.", ephemeral=True)

    @maratona.command(name="painel", description="Traz o painel da maratona para o fim do chat.")
    async def cmd_painel(self, interaction: discord.Interaction) -> None:
        m = self.exigir_aberta(interaction)
        antigo_canal, antiga_msg = m.channel_id, m.panel_message_id
        await self.enviar_painel(interaction, m)
        # Apaga o painel antigo para não ficarem dois painéis com botões.
        canal = self.bot.get_channel(antigo_canal)
        if canal is not None and antiga_msg is not None:
            try:
                await canal.get_partial_message(antiga_msg).delete()
            except discord.HTTPException:
                pass

    @maratona.command(name="sortear", description="Gira a roleta e sorteia o próximo jogo.")
    async def cmd_sortear(self, interaction: discord.Interaction) -> None:
        await self.sortear(interaction, self.exigir_aberta(interaction))

    @maratona.command(name="encerrar", description="Encerra a maratona e mostra o resumo da noite.")
    async def cmd_encerrar(self, interaction: discord.Interaction) -> None:
        await self.pedir_encerramento(interaction, self.exigir_aberta(interaction))

    @maratona.command(name="adicionar", description="Coloca um jogo na roleta (com sugestões da Steam enquanto digita).")
    @app_commands.describe(jogo="Comece a digitar e escolha da lista")
    async def cmd_adicionar(self, interaction: discord.Interaction,
                            jogo: app_commands.Range[str, 1, 100]) -> None:
        m = self.exigir_aberta(interaction)
        await self.receber_nomes(interaction, [jogo], self.concluidor_roleta(m.id))

    @cmd_adicionar.autocomplete("jogo")
    async def _autocompletar_roleta(self, interaction: discord.Interaction, atual: str):
        return await autocompletar_jogo(self.bot, interaction, atual)

    @maratona.command(name="incluir", description="Coloca alguém na maratona mesmo sem jogo (chegou no meio).")
    @app_commands.describe(pessoa="Quem chegou")
    async def cmd_incluir(self, interaction: discord.Interaction, pessoa: discord.Member) -> None:
        m = self.exigir_aberta(interaction)
        if not self.pode_organizar(interaction, m):
            raise ErroMaratona(f"Só quem organizou (<@{m.host_id}>) pode incluir outras pessoas. "
                               "Cada um pode entrar sozinho pelo botão **🙋 Tô jogando**.")
        if pessoa.bot:
            raise ErroMaratona("Bots não jogam maratona. 🤖")
        if not self.db.entrar_sem_jogo(m.id, pessoa.id):
            raise ErroMaratona(f"{pessoa.mention} já está jogando.")
        await interaction.response.send_message(f"🙋 {pessoa.mention} entrou na maratona!", allowed_mentions=SEM_PING)
        await self.atualizar_painel(m.id)

    @maratona.command(name="excluir", description="Tira da maratona alguém que entrou sem jogo.")
    @app_commands.describe(pessoa="Quem saiu")
    async def cmd_excluir(self, interaction: discord.Interaction, pessoa: discord.Member) -> None:
        m = self.exigir_aberta(interaction)
        if not self.pode_organizar(interaction, m) and pessoa.id != interaction.user.id:
            raise ErroMaratona(f"Só quem organizou (<@{m.host_id}>) pode tirar outras pessoas.")
        self.db.sair_sem_jogo(m.id, pessoa.id)
        await interaction.response.send_message(f"👋 {pessoa.mention} saiu da maratona.", allowed_mentions=SEM_PING)
        await self.atualizar_painel(m.id)

    @maratona.command(name="baratos", description="Jogos multiplayer ordenados pelo custo para o grupo da maratona.")
    async def cmd_baratos(self, interaction: discord.Interaction) -> None:
        m = self.exigir_aberta(interaction)
        steam = self.bot.get_cog("SteamCog")
        if steam is None:
            raise ErroMaratona("A parte da Steam do bot não está ligada.")
        await steam.mostrar_baratos(interaction, m)

    @maratona.command(name="ajuda", description="Explica como a maratona funciona.")
    async def cmd_ajuda(self, interaction: discord.Interaction) -> None:
        e = discord.Embed(title="🎮 Como funciona a maratona", color=COR_PAINEL, description=(
            "**1.** Alguém abre a maratona com `/maratona criar`.\n"
            "**2.** Cada um clica em **➕ Adicionar jogos** e escreve seus jogos, um por linha "
            "(ou usa `/maratona adicionar`, que sugere o nome certinho enquanto digita). "
            "Os jogos ficam secretos até serem sorteados.\n"
            "**3.** Quem organizou clica em **🎲 Sortear**. O jogo que cair é o que todo mundo joga.\n"
            "**Dica:** lembrou de um jogo no meio da semana? Use `/lista adicionar` para guardar. "
            "Na maratona, sua lista aparece quando você clica em **Adicionar jogos**.\n"
            "**4.** Reclamou do jogo? Use `/reclamou` (ou clique com o botão direito na pessoa → "
            "Apps → **Reclamou!**). Vai pro **Mural da Vergonha**. 😤\n"
            "**5.** No fim, **🏁 Encerrar** mostra o resumo da noite.\n\n"
            "Chegou no meio? Clique em **🙋 Tô jogando** no painel.\n"
            "Não querem gastar? `/maratona baratos` mostra o que sai mais barato para o grupo.\n"
            "Jogo em equipe? `/times` sorteia os times.\n"
            "Sem ideia de jogo? `/sugestao` e `/radar ver` dão sugestões.\n\n"
            "Perdeu o painel no meio do chat? `/maratona painel` traz ele de volta.\n"
            "Quer tirar um jogo seu antes de ser sorteado? **📋 Meus jogos**."
        ))
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ---- sorteio ---------------------------------------------------

    def embed_roleta(self, texto: str) -> discord.Embed:
        return discord.Embed(title="🎰 Girando a roleta…", description=texto, color=COR_ROLETA)

    async def sortear(self, interaction: discord.Interaction, m: Maratona) -> None:
        if not self.pode_organizar(interaction, m):
            raise ErroMaratona(f"Só quem organizou (<@{m.host_id}>) pode sortear.")
        if m.id in self.girando:
            raise ErroMaratona("Calma, a roleta já está girando! 🎰")

        sorteio = self.db.sortear(m.id)  # o resultado fica gravado antes da animação
        if sorteio is None:
            raise ErroMaratona("A roleta está vazia! Coloquem mais jogos ou encerrem a maratona. 🏁")

        self.girando.add(m.id)
        try:
            # Enquanto a roleta gira, o bot descobre quem tem o jogo (aproveita os segundos da animação).
            quem_tem = asyncio.create_task(self.quem_tem(m.id, sorteio.jogo.nome, sorteio.jogo.appid))
            await self.animar_roleta(interaction, m, sorteio.jogo, sorteio.numero, sorteio.restantes, quem_tem)
        finally:
            self.girando.discard(m.id)
        await self.atualizar_painel(m.id)

    async def animar_roleta(self, interaction: discord.Interaction, m: Maratona,
                            jogo: Jogo, numero: int, restantes: int,
                            quem_tem: Optional[asyncio.Task] = None) -> None:
        pessoas = [p.user_id for p in self.db.participantes(m.id)]
        pergunta = f"De quem será o jogo **#{numero}**?\n\n"
        anterior = None

        await interaction.response.send_message(embed=self.embed_roleta(pergunta + "➡️ …"),
                                                allowed_mentions=SEM_PING)
        try:
            for pausa in TEMPOS_ROLETA:
                await asyncio.sleep(pausa)
                opcoes = [p for p in pessoas if p != anterior] or pessoas
                anterior = random.choice(opcoes)
                await interaction.edit_original_response(embed=self.embed_roleta(pergunta + f"➡️ <@{anterior}>"))
            await asyncio.sleep(SUSPENSE_FINAL)
            await interaction.edit_original_response(
                embed=self.embed_roleta(f"Parou em <@{jogo.user_id}>! 👀\n\nE o jogo é…"))
            await asyncio.sleep(SUSPENSE_FINAL)
        except discord.HTTPException as exc:
            log.warning("Animação da roleta interrompida: %s", exc)

        revelacao = discord.Embed(
            title=f"🎮 {jogo.nome}", color=COR_ROLETA,
            description=f"Jogo **#{numero}** da noite, escolhido por <@{jogo.user_id}>.\n\n"
                        "**Caiu, jogou. Sem reclamar!** 🫡",
        )
        if quem_tem is not None:
            try:
                info = await asyncio.wait_for(quem_tem, timeout=8)
            except asyncio.TimeoutError:
                info = None
            if info is not None:
                opcao, sem_steam = info
                revelacao.url = opcao.app.url
                revelacao.add_field(name="💰 Quem tem", value=resumo_grupo(opcao, sem_steam)[:1024], inline=False)
        revelacao.set_footer(text="A roleta está vazia!" if restantes == 0
                             else f"Ainda {plural(restantes, 'jogo', 'jogos')} na roleta")
        try:
            await interaction.edit_original_response(embed=revelacao)
        except discord.HTTPException:
            # Se a mensagem da roleta sumiu, o resultado ainda aparece.
            await interaction.channel.send(embed=revelacao, allowed_mentions=SEM_PING)

    # ---- encerramento ----------------------------------------------

    async def pedir_encerramento(self, interaction: discord.Interaction, m: Maratona) -> None:
        if not self.pode_organizar(interaction, m):
            raise ErroMaratona(f"Só quem organizou (<@{m.host_id}>) pode encerrar a maratona.")
        restantes, _ = self.db.contagem(m.id)
        aviso = f" Ainda tem {plural(restantes, 'jogo', 'jogos')} na roleta." if restantes else ""
        await interaction.response.send_message(f"Encerrar **{m.nome}**?{aviso} Não dá para desfazer.",
                                                view=ConfirmarEncerramentoView(self, m), ephemeral=True)

    async def encerrar(self, interaction: discord.Interaction, m: Maratona) -> None:
        m = self.db.encerrar(m.id)
        await interaction.response.edit_message(content="🏁 Maratona encerrada!", view=None)
        await interaction.channel.send(embed=self.embed_resumo(m), allowed_mentions=SEM_PING)
        await self.atualizar_painel(m.id)

    def embed_resumo(self, m: Maratona) -> discord.Embed:
        jogos = self.db.jogos(m.id)
        jogados = sorted((j for j in jogos if j.jogado), key=lambda j: j.ordem)
        sobras = [j for j in jogos if not j.jogado]
        mural = self.db.mural(m.id)

        e = discord.Embed(
            title=f"🏁 Fim da maratona: {m.nome}", color=COR_FIM,
            description=f"Foram **{plural(len(jogados), 'jogo', 'jogos')}** em "
                        f"**{duracao(m.criada_em, m.encerrada_em)}**. GG a todos! 🎉",
        )
        e.add_field(name="🎮 O que jogamos",
                    value=juntar([f"`{j.ordem}.` **{j.nome}** — <@{j.user_id}>" for j in jogados],
                                 vazio="Nenhum jogo foi sorteado."), inline=False)
        if sobras:
            e.add_field(name="📦 Ficaram na roleta (revelados!)",
                        value=juntar([f"**{j.nome}** — <@{j.user_id}>" for j in sobras]), inline=False)
            e.set_footer(text="Os jogos que não saíram voltaram para a lista de quem colocou (/lista ver).")
        e.add_field(name="😤 Mural da Vergonha",
                    value=juntar(ranking(mural), vazio="Ninguém reclamou. Que grupo exemplar! 😇"), inline=False)
        return e

    # ---- /lista ----------------------------------------------------

    @lista.command(name="adicionar", description="Guarda jogos na sua lista para a próxima maratona.")
    @app_commands.describe(jogo="Nome do jogo (aparecem sugestões enquanto digita; vazio = escrever vários)")
    async def cmd_lista_adicionar(self, interaction: discord.Interaction,
                                  jogo: Optional[app_commands.Range[str, 1, 100]] = None) -> None:
        if jogo is None:
            await interaction.response.send_modal(SalvarNaListaModal(self))
        else:
            await self.receber_nomes(interaction, [jogo], self.concluir_lista)

    @cmd_lista_adicionar.autocomplete("jogo")
    async def _autocompletar_lista(self, interaction: discord.Interaction, atual: str):
        return await autocompletar_jogo(self.bot, interaction, atual)

    @lista.command(name="ver", description="Mostra (só para você) os jogos da sua lista.")
    async def cmd_lista_ver(self, interaction: discord.Interaction) -> None:
        view = MinhaListaView(self, interaction.user.id)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    # ---- mural da vergonha -----------------------------------------

    @app_commands.command(name="reclamou", description="Dedura alguém que reclamou do jogo sorteado.")
    @app_commands.describe(pessoa="Quem reclamou")
    @app_commands.guild_only()
    async def cmd_reclamou(self, interaction: discord.Interaction, pessoa: discord.Member) -> None:
        await self.dedurar(interaction, pessoa)

    async def _menu_reclamou(self, interaction: discord.Interaction, pessoa: discord.Member) -> None:
        try:
            await self.dedurar(interaction, pessoa)
        except Exception as erro:  # menus de contexto não passam pelo cog_app_command_error
            await tratar_erro(interaction, erro)

    async def dedurar(self, interaction: discord.Interaction, pessoa: discord.abc.User) -> None:
        m = self.exigir_aberta(interaction)
        if pessoa.bot:
            raise ErroMaratona("Bots não reclamam. Bots obedecem. 🤖")
        total = self.db.registrar_reclamacao(m.id, pessoa.id, interaction.user.id)
        atual = self.db.ultimo_sorteado(m.id)
        de_que = f" de **{atual.nome}**" if atual else ""

        if pessoa.id == interaction.user.id:
            texto = f"🙊 {pessoa.mention} **confessou** que reclamou{de_que}! Honestidade é bonito, mas o ponto vai pro mural."
        else:
            texto = f"📢 {interaction.user.mention} dedurou {pessoa.mention} por reclamar{de_que}!"
        texto += f"\n😤 {pessoa.mention} agora tem **{plural(total, 'reclamação', 'reclamações')}** no Mural da Vergonha."

        await interaction.response.send_message(texto, allowed_mentions=SEM_PING)
        await self.atualizar_painel(m.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MaratonaCog(bot))
