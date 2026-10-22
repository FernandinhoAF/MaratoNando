"""Comandos, painel e roleta da maratona de jogos."""

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import cartoes
from biblioteca import resumo_grupo, rodape_card
import modos
import estatisticas
from marca import ASSINATURA, COR_DETALHE, COR_FIM, COR_PAINEL, COR_ROLETA, COR_VERGONHA, url_nando
from nomes import ler_escolha, valor_escolha
from notas import MINIMO_NOTAS_RANKING, estrelas, numero, resumo_nota
from notas import ranking as ranking_notas
from db import Database, ErroMaratona, ItemLista, Jogo, Maratona, limpar_nome, separar_nomes

log = logging.getLogger("maratonando")

# As cores (COR_PAINEL, COR_ROLETA…) vêm do marca.py, seguindo o Manual da Marca.

# Pausas (em segundos) entre os quadros da roleta: vai desacelerando, como uma roleta de verdade.
TEMPOS_ROLETA = (0.6, 0.8, 1.1, 1.4)
TEMPO_MAX_MODO = 45  # segundos para conferir na Steam os jogos da roleta (modos); o que passar disso fica de fora
SUSPENSE_FINAL = 1.6

MEDALHAS = ("🥇", "🥈", "🥉")
# Menu "Mais opções…" do painel: (valor, rótulo, emoji, descrição).
OPCOES_MAIS = [
    ("modo", "Modo da roleta", "🎯", "Quais jogos podem sair no sorteio (quem organizou)"),
    ("baratos", "Mais baratos para o grupo", "💰", "Jogos multiplayer do mais barato para o grupo"),
    ("times", "Sortear times", "👥", "Divide quem está jogando em times"),
    ("steam", "Quem tem a Steam vinculada", "🎮", "As contas da Steam de quem está jogando"),
    ("incluir", "Incluir alguém", "➕", "Coloca na maratona quem chegou sem jogo"),
    ("tirar", "Tirar alguém sem jogo", "➖", "Tira quem tinha entrado sem jogo"),
    ("painel", "Trazer o painel para baixo", "📌", "Posta o painel de novo no fim do chat"),
    ("ajuda", "Como funciona", "❓", "Explica a maratona"),
]
ESCOLHAS_MODO = [
    app_commands.Choice(name="🎲 Livre (qualquer jogo)", value="livre"),
    app_commands.Choice(name="✅ Ninguém precisa comprar (todo mundo tem ou é grátis)", value="todos_tem"),
    app_commands.Choice(name="💸 Só até R$ X (preencha o preço)", value="ate_preco"),
]
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

    @discord.ui.select(custom_id="maratonando:mais", placeholder="Mais opções…", row=1, options=[
        discord.SelectOption(label=rotulo, value=valor, emoji=emoji, description=desc)
        for valor, rotulo, emoji, desc in OPCOES_MAIS])
    async def mais(self, interaction: discord.Interaction, menu: discord.ui.Select) -> None:
        if m := await self._maratona(interaction):
            await self.cog.opcao_do_painel(interaction, m, menu.values[0])


class HubMaratonaView(discord.ui.View):
    """O /maratona quando não tem maratona aberta: abrir agora, agendar ou ver como funciona."""

    def __init__(self, cog: "MaratonaCog") -> None:
        super().__init__(timeout=600)
        self.cog = cog

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    @discord.ui.button(label="Abrir agora", emoji="🎮", style=discord.ButtonStyle.success)
    async def abrir(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(AbrirMaratonaModal(self.cog))

    @discord.ui.button(label="Agendar", emoji="📅", style=discord.ButtonStyle.primary)
    async def agendar(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_modal(AgendarMaratonaModal(self.cog))

    @discord.ui.button(label="Como funciona", emoji="❓", style=discord.ButtonStyle.secondary)
    async def ajuda(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=self.cog.embed_ajuda_maratona(), ephemeral=True)


class AbrirMaratonaModal(discord.ui.Modal, title="Abrir a maratona"):
    nome = discord.ui.TextInput(label="Nome da maratona", placeholder="Maratona de Jogos", required=False,
                                max_length=60)
    limite = discord.ui.TextInput(label="Máximo de jogos por pessoa (vazio = sem limite)", placeholder="3",
                                  required=False, max_length=2)

    def __init__(self, cog: "MaratonaCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        limite = None
        if self.limite.value.strip():
            if not self.limite.value.strip().isdigit() or not 1 <= int(self.limite.value) <= 25:
                raise ErroMaratona("O limite de jogos por pessoa vai de 1 a 25 (ou fica vazio).")
            limite = int(self.limite.value)
        await self.cog.abrir_maratona(interaction, self.nome.value.strip() or None, limite)


class AgendarMaratonaModal(discord.ui.Modal, title="Agendar a maratona"):
    dia = discord.ui.TextInput(label="Dia", placeholder="sábado, amanhã, 25/10…", max_length=20)
    hora = discord.ui.TextInput(label="Hora (de Brasília)", placeholder="21h, 21:30…", max_length=10)
    nome = discord.ui.TextInput(label="Nome da maratona", placeholder="Maratona de Jogos", required=False,
                                max_length=60)

    def __init__(self, cog: "MaratonaCog") -> None:
        super().__init__()
        self.cog = cog

    async def on_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        agenda_cog = self.cog.bot.get_cog("AgendaCog")
        if agenda_cog is None:
            raise ErroMaratona("A parte de agendamento do bot não está ligada.")
        # Se quem agendou está numa call, o evento do calendário aponta para ela.
        call = getattr(getattr(interaction.user, "voice", None), "channel", None)
        await agenda_cog.agendar(interaction, self.dia.value, self.hora.value, self.nome.value.strip() or None,
                                 call if isinstance(call, discord.VoiceChannel) else None)


class ModoView(discord.ui.View):
    """Escolha do modo da roleta (menu "Mais…" → Modo da roleta)."""

    def __init__(self, cog: "MaratonaCog", maratona_id: int) -> None:
        super().__init__(timeout=300)
        self.cog, self.maratona_id = cog, maratona_id

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    @discord.ui.select(placeholder="Escolha o modo…", options=[
        discord.SelectOption(label=c.name, value=c.value) for c in ESCOLHAS_MODO])
    async def escolher(self, interaction: discord.Interaction, menu: discord.ui.Select) -> None:
        if menu.values[0] == "ate_preco":
            await interaction.response.send_modal(PrecoModal(self.cog, self.maratona_id))
        else:
            await self.cog.aplicar_modo(interaction, self.maratona_id, menu.values[0], None)


class PrecoModal(discord.ui.Modal, title="Só jogos de até quanto?"):
    preco = discord.ui.TextInput(label="Preço máximo, em reais", placeholder="20", max_length=3)

    def __init__(self, cog: "MaratonaCog", maratona_id: int) -> None:
        super().__init__()
        self.cog, self.maratona_id = cog, maratona_id

    async def on_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        valor = self.preco.value.strip().replace("R$", "").strip()
        if not valor.isdigit() or not 1 <= int(valor) <= 500:
            raise ErroMaratona("Escreva só o número de reais, de 1 a 500 (ex.: 20).")
        await self.cog.aplicar_modo(interaction, self.maratona_id, "ate_preco", int(valor))


class PessoasView(discord.ui.View):
    """Incluir na maratona quem chegou sem jogo, ou tirar quem tinha entrado sem jogo."""

    def __init__(self, cog: "MaratonaCog", maratona_id: int, acao: str) -> None:
        super().__init__(timeout=300)
        self.cog, self.maratona_id, self.acao = cog, maratona_id, acao
        self.quem.placeholder = "Quem chegou?" if acao == "incluir" else "Quem saiu?"

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    @discord.ui.select(cls=discord.ui.UserSelect, min_values=1, max_values=10)
    async def quem(self, interaction: discord.Interaction, menu: discord.ui.UserSelect) -> None:
        await self.cog.incluir_ou_tirar(interaction, self.maratona_id, self.acao, list(menu.values))


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
                placeholder="🗑️ Tirar jogos da lista (pode marcar vários)…",
                min_values=1, max_values=len(itens),
                options=[discord.SelectOption(label=i.nome[:100], value=str(i.id), emoji="🗑️") for i in itens],
            )
            menu.callback = self.remover
            self.add_item(menu)
        adicionar = discord.ui.Button(label="Adicionar jogos", emoji="➕", style=discord.ButtonStyle.success)
        adicionar.callback = self.adicionar
        self.add_item(adicionar)
        grupo = discord.ui.Button(label="Lista do grupo", emoji="🎁", style=discord.ButtonStyle.secondary)
        grupo.callback = self.grupo
        self.add_item(grupo)
        if self.nao_ligados():
            botao = discord.ui.Button(label="Conferir nomes na Steam", emoji="🔎", style=discord.ButtonStyle.primary)
            botao.callback = self.conferir
            self.add_item(botao)

    async def adicionar(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(SalvarNaListaModal(self.cog))

    async def grupo(self, interaction: discord.Interaction) -> None:
        if interaction.guild_id is None:
            raise ErroMaratona("A lista do grupo só existe dentro de um servidor.")
        steam = self.cog.bot.get_cog("SteamCog")
        if steam is None:
            raise ErroMaratona("A parte da Steam do bot não está ligada.")
        await steam.mostrar_desejos(interaction)

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
                               vazio="Sua lista está vazia. Clique em **➕ Adicionar jogos** para guardar jogos."),
        )
        e.set_footer(text="Só você vê esta lista. Na maratona, ela aparece quando você clica em Adicionar jogos.")
        return e

    async def remover(self, interaction: discord.Interaction) -> None:
        menu: discord.ui.Select = self.children[0]  # type: ignore[assignment]
        nomes = [self.cog.db.remover_da_lista(self.user_id, int(v)).nome for v in menu.values]
        self.montar()
        await interaction.response.edit_message(
            content="🗑️ Saiu da sua lista: " + ", ".join(f"**{n}**" for n in nomes),
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
        e.set_thumbnail(url=url_nando(self.cog.bot))
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

class BotaoNota(discord.ui.DynamicItem[discord.ui.Button], template=r"nota:(?P<jogo>\d+):(?P<nota>[1-5])"):
    """Um dos 5 botões de nota de um jogo. O número do jogo e a nota ficam no próprio custom_id,
    então os botões continuam funcionando mesmo depois que o bot reinicia."""

    def __init__(self, jogo_id: int, nota: int) -> None:
        super().__init__(discord.ui.Button(label=str(nota), emoji="⭐", style=discord.ButtonStyle.secondary,
                                           custom_id=f"nota:{jogo_id}:{nota}"))
        self.jogo_id = jogo_id
        self.nota = nota

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["jogo"]), int(match["nota"]))

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: "MaratonaCog" = interaction.client.get_cog("MaratonaCog")  # type: ignore[assignment]
        try:
            await cog.receber_nota(interaction, self.jogo_id, self.nota)
        except Exception as erro:
            await tratar_erro(interaction, erro)


def view_notas(jogo_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for nota in range(1, 6):
        view.add_item(BotaoNota(jogo_id, nota))
    return view


class MaratonaCog(commands.Cog):
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
        self.bot.add_dynamic_items(BotaoNota)  # e os botões de nota
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
            raise ErroMaratona("Não tem nenhuma maratona aberta agora. Use `/maratona` para começar uma!")
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
                f"**Regra de ouro:** {ASSINATURA} 🫡\n\n"
                f"Organização: <@{m.host_id}> · aberta {carimbo(m.criada_em)}"
            )
            if m.limite:
                descricao += f"\nLimite: **{m.limite}** jogos por pessoa"
            if m.modo != "livre":
                descricao += f"\n🎯 Modo da roleta: **{modos.descricao(m.modo, m.modo_preco)}**"
        else:
            descricao = f"🏁 **Maratona encerrada** {carimbo(m.encerrada_em)}. Valeu, galera!"

        e = discord.Embed(title=f"🎮 {m.nome}", description=descricao,
                          color=COR_PAINEL if m.aberta else discord.Color.dark_grey())
        e.set_author(name="MARATONA ABERTA" if m.aberta else "MARATONA ENCERRADA",
                     icon_url=url_nando(self.bot))
        e.set_thumbnail(url=url_nando(self.bot))
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

    async def enviar_painel_no_canal(self, canal: discord.abc.Messageable, m: Maratona, texto: Optional[str] = None,
                                     mencoes: discord.AllowedMentions = SEM_PING) -> None:
        """Igual ao enviar_painel, mas sem ninguém ter clicado em nada (ex.: maratona agendada que abriu sozinha)."""
        msg = await canal.send(texto, embed=self.embed_painel(m), view=self.painel_view, allowed_mentions=mencoes)
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
        """Caminho único para jogos digitados (formulário, /maratona jogo:…, /lista jogo:…).

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
        """Botão "Conferir nomes na Steam" do /lista: liga os jogos da lista à Steam, corrigindo os nomes."""
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
            texto = ("🙋 Você entrou na maratona! Já conta nos times e nos mais baratos do grupo. "
                     "Se quiser colocar jogo na roleta, é só clicar em **Adicionar jogos**.")
        else:
            texto = "Você já está jogando (colocou jogos na roleta). 👍"
        await interaction.response.send_message(texto, ephemeral=True)
        await self.atualizar_painel(m.id)

    async def mostrar_meus_jogos(self, interaction: discord.Interaction, m: Maratona) -> None:
        view = MeusJogosView(self, m, interaction.user.id)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    # ---- /maratona -------------------------------------------------

    @app_commands.command(name="maratona",
                          description="Abre, agenda ou mostra a maratona (e coloca um jogo na roleta, se quiser).")
    @app_commands.describe(jogo="Opcional: um jogo para colocar na roleta (aparecem sugestões da Steam)")
    @app_commands.guild_only()
    async def cmd_maratona(self, interaction: discord.Interaction,
                           jogo: Optional[app_commands.Range[str, 1, 100]] = None) -> None:
        m = self.db.maratona_aberta(interaction.guild_id)
        if jogo:
            if m is None:
                raise ErroMaratona("Não tem maratona aberta agora. Use `/maratona` (sem nada) para abrir uma.")
            await self.receber_nomes(interaction, [jogo], self.concluidor_roleta(m.id))
            return
        if m is not None:  # já tem maratona: traz o painel para o fim do chat
            await self.trazer_painel(interaction, m)
            return
        e = discord.Embed(title="🎮 Maratona de jogos", color=COR_PAINEL, description=(
            "Não tem maratona aberta agora.\n\n"
            "**🎮 Abrir agora** começa uma maratona neste canal.\n"
            "**📅 Agendar** marca para depois: o bot posta um convite com \"Eu vou\", coloca no calendário "
            "do servidor e abre sozinho 15 minutos antes.\n\n"
            f"**Regra de ouro:** {ASSINATURA} 🫡"))
        e.set_thumbnail(url=url_nando(self.bot))
        await interaction.response.send_message(embed=e, view=HubMaratonaView(self), ephemeral=True)

    @cmd_maratona.autocomplete("jogo")
    async def _autocompletar_roleta(self, interaction: discord.Interaction, atual: str):
        return await autocompletar_jogo(self.bot, interaction, atual)

    async def abrir_maratona(self, interaction: discord.Interaction, nome: Optional[str],
                             limite: Optional[int], avisar: bool = True) -> None:
        aberta = self.db.maratona_aberta(interaction.guild_id)
        if aberta:
            raise ErroMaratona(f"Já tem uma maratona aberta em <#{aberta.channel_id}>. "
                               "Use `/maratona` para trazer o painel dela.")
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

    async def trazer_painel(self, interaction: discord.Interaction, m: Maratona) -> None:
        """Posta o painel de novo no fim do chat e apaga o antigo (para não ficarem dois com botões)."""
        antigo_canal, antiga_msg = m.channel_id, m.panel_message_id
        await self.enviar_painel(interaction, m)
        canal = self.bot.get_channel(antigo_canal)
        if canal is not None and antiga_msg is not None:
            try:
                await canal.get_partial_message(antiga_msg).delete()
            except discord.HTTPException:
                pass

    # ---- menu "Mais opções…" do painel -------------------------------

    async def opcao_do_painel(self, interaction: discord.Interaction, m: Maratona, opcao: str) -> None:
        if opcao == "painel":
            await self.trazer_painel(interaction, m)
            return  # o painel novo já vem com o menu limpo
        if opcao == "modo":
            if not self.pode_organizar(interaction, m):
                raise ErroMaratona(f"Só quem organizou (<@{m.host_id}>) pode mudar o modo da roleta.")
            await interaction.response.send_message(
                f"🎯 Modo atual: **{modos.descricao(m.modo, m.modo_preco)}**\nQuais jogos podem sair no sorteio?",
                view=ModoView(self, m.id), ephemeral=True)
        elif opcao == "baratos":
            steam = self.bot.get_cog("SteamCog")
            if steam is None:
                raise ErroMaratona("A parte da Steam do bot não está ligada.")
            await steam.mostrar_baratos(interaction, m)
        elif opcao == "times":
            times = self.bot.get_cog("TimesCog")
            if times is None:
                raise ErroMaratona("A parte de times do bot não está ligada.")
            await times.abrir(interaction)
        elif opcao == "steam":
            steam = self.bot.get_cog("SteamCog")
            if steam is None:
                raise ErroMaratona("A parte da Steam do bot não está ligada.")
            await steam.mostrar_contas(interaction)
        elif opcao in ("incluir", "tirar"):
            if opcao == "incluir" and not self.pode_organizar(interaction, m):
                raise ErroMaratona(f"Só quem organizou (<@{m.host_id}>) pode incluir outras pessoas. "
                                   "Cada um pode entrar sozinho pelo botão **🙋 Tô jogando**.")
            await interaction.response.send_message(
                "Escolha as pessoas:", view=PessoasView(self, m.id, opcao), ephemeral=True)
        else:  # ajuda
            await interaction.response.send_message(embed=self.embed_ajuda_maratona(), ephemeral=True)
        # Volta o menu para "Mais opções…" (senão escolher a mesma opção de novo não funciona).
        self.em_segundo_plano(self.atualizar_painel(m.id))

    async def incluir_ou_tirar(self, interaction: discord.Interaction, maratona_id: int, acao: str,
                               pessoas: list) -> None:
        m = self.db.maratona(maratona_id)
        if m is None or not m.aberta:
            raise ErroMaratona("Essa maratona já terminou. 🏁")
        feitos, avisos = [], []
        for pessoa in pessoas:
            if pessoa.bot:
                avisos.append(f"{pessoa.mention}: bots não jogam maratona. 🤖")
            elif acao == "incluir":
                if self.db.entrar_sem_jogo(m.id, pessoa.id):
                    feitos.append(pessoa.mention)
                else:
                    avisos.append(f"{pessoa.mention} já está jogando.")
            else:
                if not self.pode_organizar(interaction, m) and pessoa.id != interaction.user.id:
                    avisos.append(f"{pessoa.mention}: só quem organizou (<@{m.host_id}>) pode tirar outras pessoas.")
                    continue
                try:
                    self.db.sair_sem_jogo(m.id, pessoa.id)
                    feitos.append(pessoa.mention)
                except ErroMaratona as erro:
                    avisos.append(f"{pessoa.mention}: {erro}")
        if not feitos:
            raise ErroMaratona("\n".join(avisos) or "Ninguém mudou.")
        texto = (f"🙋 {', '.join(feitos)} {'entrou' if len(feitos) == 1 else 'entraram'} na maratona!"
                 if acao == "incluir" else
                 f"👋 {', '.join(feitos)} {'saiu' if len(feitos) == 1 else 'saíram'} da maratona.")
        if avisos:
            texto += "\n-# " + " · ".join(avisos)
        await interaction.response.send_message(texto, allowed_mentions=SEM_PING)
        await self.atualizar_painel(m.id)

    def validar_modo(self, modo: str, preco: Optional[int]) -> Optional[int]:
        try:
            centavos = modos.validar(modo, preco)
        except ValueError as erro:
            raise ErroMaratona(str(erro)) from None
        steam = self.bot.get_cog("SteamCog")
        if modo != "livre" and (steam is None or steam.api is None):
            raise ErroMaratona("Esse modo precisa da Steam (para saber quem tem cada jogo), mas a chave da Steam "
                               "não está configurada no bot.")
        return centavos

    async def aplicar_modo(self, interaction: discord.Interaction, maratona_id: int, modo: str,
                           preco_reais: Optional[int]) -> None:
        m = self.db.maratona(maratona_id)
        if m is None or not m.aberta:
            raise ErroMaratona("Essa maratona já terminou. 🏁")
        if not self.pode_organizar(interaction, m):
            raise ErroMaratona(f"Só quem organizou (<@{m.host_id}>) pode mudar o modo da roleta.")
        centavos = self.validar_modo(modo, preco_reais)
        m = self.db.definir_modo(m.id, modo, centavos)
        explicacao = {
            "livre": "Qualquer jogo da roleta pode sair.",
            "todos_tem": "Só saem jogos que todo mundo (com Steam vinculada) já tem, ou que são grátis.",
            "ate_preco": "Só saem jogos que custam até esse valor para quem ainda não tem.",
        }[modo]
        await interaction.response.send_message(
            f"🎯 Modo da roleta: **{modos.descricao(m.modo, m.modo_preco)}**\n{explicacao} "
            "Os outros jogos continuam na roleta e voltam a valer se o modo mudar.", allowed_mentions=SEM_PING)
        await self.atualizar_painel(m.id)

    # ---- ajuda -------------------------------------------------------

    def embed_ajuda_maratona(self) -> discord.Embed:
        e = discord.Embed(title="🎮 Como funciona a maratona", color=COR_PAINEL, description=(
            "**1.** Alguém usa `/maratona` e clica em **🎮 Abrir agora** (ou **📅 Agendar**, para depois).\n"
            "**2.** Cada um clica em **➕ Adicionar jogos** no painel e escreve seus jogos, um por linha. "
            "Os jogos ficam secretos até serem sorteados. Também dá para usar `/maratona jogo:…`, que sugere o "
            "nome certinho enquanto você digita.\n"
            "**3.** Quem organizou clica em **🎲 Sortear**. O jogo que cair é o que todo mundo joga.\n"
            "**4.** Reclamou do jogo? `/reclamou @pessoa` (ou botão direito na pessoa → Apps → **Reclamou!**). "
            "Vai pro **Mural da Vergonha**. 😤\n"
            "**5.** Depois de cada jogo, o bot pede uma nota de 1 a 5 ⭐.\n"
            "**6.** No fim, **🏁 Encerrar** mostra o resumo da noite.\n\n"
            "**No menu \"Mais opções…\" do painel:** modo da roleta (só jogos que todo mundo tem, ou até um "
            "preço), os mais baratos para o grupo, sortear times, quem tem a Steam vinculada, incluir ou tirar "
            "alguém e trazer o painel para baixo.\n\n"
            "Chegou no meio? **🙋 Tô jogando**. Quer tirar um jogo seu antes de sair? **📋 Meus jogos**.\n"
            "Lembrou de um jogo no meio da semana? `/lista` guarda ele para a próxima maratona.\n"
            "Todos os comandos: `/ajuda`."
        ))
        e.set_thumbnail(url=url_nando(self.bot))
        e.set_footer(text=f"MaratoNando · {ASSINATURA}")
        return e

    @app_commands.command(name="ajuda", description="Tudo o que o MaratoNando faz, num lugar só.")
    async def cmd_ajuda(self, interaction: discord.Interaction) -> None:
        e = discord.Embed(title="❓ O que o MaratoNando faz", color=COR_PAINEL, description=(
            f"**Regra de ouro:** {ASSINATURA} 🫡\n"
            "Quase tudo é por botões: os comandos abaixo abrem painéis com o resto."))
        e.add_field(name="🎮 Maratona", inline=False, value=(
            "`/maratona` abre (ou agenda) a maratona e mostra o painel: adicionar jogos, sortear, encerrar e o "
            "menu **Mais opções…** (modo da roleta, mais baratos, times…).\n"
            "`/reclamou @pessoa` manda para o Mural da Vergonha."))
        e.add_field(name="🕹️ Jogar fora da maratona", inline=False, value=(
            "`/bora` chama o pessoal para jogar (vagas, fila e horário).\n"
            "`/o-que-jogar` sugere jogos para quantas pessoas vocês forem, mais **🔥 em alta**, **🎁 grátis** e o "
            "**📡 radar dos streamers**.\n"
            "`/times` sorteia times (da maratona, da sua call ou de quem você marcar)."))
        e.add_field(name="📇 Jogos e pessoas", inline=False, value=(
            "`/jogo` mostra a ficha de um jogo: preço, avaliações, nº de jogadores e quem do servidor tem.\n"
            "`/lista` é a sua lista para as próximas maratonas (e a **🎁 lista do grupo**).\n"
            "`/perfil` mostra horas, mais jogados e gêneros favoritos (e é onde você **vincula a Steam**).\n"
            "`/stats` mostra os números do grupo, com o **🏆 ranking** dos jogos favoritos."))
        perms = getattr(interaction.user, "guild_permissions", None)
        if perms and perms.manage_guild:
            e.add_field(name="⚙️ Só para admins", inline=False, value=(
                "`/config` canal do bot, faxina, avisos, cargo de jogos e Steam dos outros.\n"
                "`/radar` streamers acompanhados, preço máximo e canal de alertas."))
        e.set_thumbnail(url=url_nando(self.bot))
        e.set_footer(text=f"MaratoNando · {ASSINATURA}")
        await interaction.response.send_message(embed=e, ephemeral=True)

    # ---- sorteio ---------------------------------------------------

    def embed_roleta(self, texto: str) -> discord.Embed:
        e = discord.Embed(title="🎰 Girando a roleta…", description=texto, color=COR_ROLETA)
        e.set_thumbnail(url=url_nando(self.bot, "surpreso"))
        return e

    async def sortear(self, interaction: discord.Interaction, m: Maratona) -> None:
        if not self.pode_organizar(interaction, m):
            raise ErroMaratona(f"Só quem organizou (<@{m.host_id}>) pode sortear.")
        if m.id in self.girando:
            raise ErroMaratona("Calma, a roleta já está girando! 🎰")
        restantes, jogados = self.db.contagem(m.id)
        if restantes == 0:
            raise ErroMaratona("A roleta está vazia! Coloquem mais jogos ou encerrem a maratona. 🏁")

        self.girando.add(m.id)
        try:
            pergunta = f"De quem será o jogo **#{jogados + 1}**?\n\n"
            await interaction.response.send_message(embed=self.embed_roleta(pergunta + "➡️ …"),
                                                    allowed_mentions=SEM_PING)
            permitidos, conferidos, aviso_modo = None, {}, ""
            if m.modo != "livre":
                # Modo ligado: confere quem tem cada jogo ANTES de sortear (a roleta já está na tela).
                await interaction.edit_original_response(embed=self.embed_roleta(
                    pergunta + f"🔎 Conferindo na Steam quais jogos valem no modo **{modos.descricao(m.modo, m.modo_preco)}**…"))
                permitidos, conferidos, aviso_modo = await self.filtrar_pelo_modo(m)
                if not permitidos:
                    await interaction.edit_original_response(embed=discord.Embed(
                        title="🎯 Nenhum jogo da roleta vale nesse modo", color=COR_VERGONHA,
                        description=f"Modo: **{modos.descricao(m.modo, m.modo_preco)}**\n{aviso_modo}\n\n"
                                    "Coloquem jogos que passem no modo, ou quem organizou muda o modo com "
                                    "**Mais opções… → 🎯 Modo da roleta**."))
                    return
            sorteio = self.db.sortear(m.id, permitidos)  # o resultado fica gravado antes do fim da animação
            if sorteio is None:
                await interaction.edit_original_response(embed=self.embed_roleta("A roleta ficou vazia! 🏁"))
                return
            # Enquanto a roleta gira, o bot descobre quem tem o jogo (se o modo já não descobriu).
            if sorteio.jogo.id in conferidos:
                quem_tem = asyncio.create_task(asyncio.sleep(0, result=conferidos[sorteio.jogo.id]))
            else:
                quem_tem = asyncio.create_task(self.quem_tem(m.id, sorteio.jogo.nome, sorteio.jogo.appid))
            await self.animar_roleta(interaction, m, sorteio.jogo, sorteio.numero, sorteio.restantes, quem_tem,
                                     aviso_modo)
        finally:
            self.girando.discard(m.id)
        await self.atualizar_painel(m.id)
        # O jogo anterior acabou de ser jogado: hora de dar nota para ele.
        anterior = self.db.jogo_por_ordem(m.id, sorteio.numero - 1)
        if anterior is not None:
            await self.pedir_notas(interaction.channel, anterior, proximo=sorteio.jogo.nome)

    async def filtrar_pelo_modo(self, m: Maratona) -> tuple[set[int], dict, str]:
        """(ids dos jogos que podem sair, {id: quem tem} do que foi conferido, aviso sobre os que ficaram de fora)."""
        pendentes = [j for j in self.db.jogos(m.id) if not j.jogado]
        conferidos: dict[int, object] = {}
        permitidos: set[int] = set()
        nao_conferidos = 0
        limite = asyncio.get_running_loop().time() + TEMPO_MAX_MODO
        for j in pendentes:
            info = None
            falta = limite - asyncio.get_running_loop().time()
            if falta > 0:
                try:
                    info = await asyncio.wait_for(self.quem_tem(m.id, j.nome, j.appid), timeout=falta)
                except asyncio.TimeoutError:
                    info = None
            if info is None:
                nao_conferidos += 1
            else:
                conferidos[j.id] = info
            if modos.passa(m.modo, m.modo_preco, info[0] if info else None):
                permitidos.add(j.id)
        nao_passam = len(pendentes) - len(permitidos) - nao_conferidos
        partes = []
        if nao_passam:
            partes.append(f"{plural(nao_passam, 'jogo não passa', 'jogos não passam')} no modo")
        if nao_conferidos:
            partes.append(f"{plural(nao_conferidos, 'jogo não deu', 'jogos não deram')} para conferir na Steam")
        if partes:
            partes[-1] += " (continuam na roleta)"
        return permitidos, conferidos, (" · ".join(partes) + ".") if partes else ""

    async def animar_roleta(self, interaction: discord.Interaction, m: Maratona,
                            jogo: Jogo, numero: int, restantes: int,
                            quem_tem: Optional[asyncio.Task] = None, aviso_modo: str = "") -> None:
        """Continua a animação (a primeira tela já foi mandada pelo sortear) e revela o jogo."""
        pessoas = [p.user_id for p in self.db.participantes(m.id)]
        pergunta = f"De quem será o jogo **#{numero}**?\n\n"
        anterior = None

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
                        f"**{ASSINATURA[:-1]}!** 🫡",
        )
        if m.modo != "livre":
            revelacao.add_field(name="🎯 Modo da roleta",
                                value=f"{modos.descricao(m.modo, m.modo_preco)}\n{aviso_modo}".strip()[:1024],
                                inline=False)
        rodape = ""
        if quem_tem is not None:
            try:
                info = await asyncio.wait_for(quem_tem, timeout=8)
            except asyncio.TimeoutError:
                info = None
            if info is not None:
                opcao, sem_steam = info
                revelacao.url = opcao.app.url
                revelacao.add_field(name="💰 Quem tem", value=resumo_grupo(opcao, sem_steam)[:1024], inline=False)
                rodape = rodape_card(opcao)
        revelacao.set_footer(text="A roleta está vazia!" if restantes == 0
                             else f"Ainda {plural(restantes, 'jogo', 'jogos')} na roleta")

        # O card do manual (imagem). Se der qualquer problema na imagem, o embed sai sem ela.
        arquivo = None
        try:
            quem = await self.nome_exibido(interaction.guild, jogo.user_id)
            png = await asyncio.to_thread(cartoes.card_roleta, numero, jogo.nome, quem, rodape, restantes)
            arquivo = discord.File(png, filename="roleta.png")
            revelacao.set_image(url="attachment://roleta.png")
        except Exception:
            log.exception("Não consegui gerar o card da roleta")
        anexos = [arquivo] if arquivo else []
        try:
            await interaction.edit_original_response(embed=revelacao, attachments=anexos)
        except discord.HTTPException:
            # Se a mensagem da roleta sumiu, o resultado ainda aparece.
            if arquivo:
                arquivo.reset()
            await interaction.channel.send(embed=revelacao, files=anexos, allowed_mentions=SEM_PING)

    async def nome_exibido(self, guild: Optional[discord.Guild], user_id: int) -> str:
        """O nome que a pessoa usa no servidor (para escrever nas imagens, onde menção não funciona)."""
        membro = guild.get_member(user_id) if guild else None
        if membro is None and guild is not None:
            try:
                membro = await guild.fetch_member(user_id)
            except discord.HTTPException:
                membro = None
        if membro is not None:
            return membro.display_name
        usuario = self.bot.get_user(user_id)
        return usuario.display_name if usuario else "alguém"

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
        ultimo = self.db.ultimo_sorteado(m.id)
        if ultimo is not None:
            await self.pedir_notas(interaction.channel, ultimo)
        e = self.embed_resumo(m)
        arquivo = await self.card_resumo(interaction.guild, m)
        if arquivo:
            e.set_image(url="attachment://resumo.png")
        await interaction.channel.send(embed=e, files=[arquivo] if arquivo else [], allowed_mentions=SEM_PING)
        await self.atualizar_painel(m.id)

    async def card_resumo(self, guild: Optional[discord.Guild], m: Maratona) -> Optional[discord.File]:
        """A imagem "tabela de recordes" do fim da maratona (Manual da Marca, pág. 7)."""
        try:
            jogados = sorted((j for j in self.db.jogos(m.id) if j.jogado), key=lambda j: j.ordem)
            jogos = [(j.nome, await self.nome_exibido(guild, j.user_id)) for j in jogados]
            mural = [(await self.nome_exibido(guild, uid), n) for uid, n in self.db.mural(m.id)]
            cabecalho = f"{plural(len(jogados), 'JOGO', 'JOGOS')} · {duracao(m.criada_em, m.encerrada_em).upper()}"
            png = await asyncio.to_thread(cartoes.card_resumo, m.nome, jogos, cabecalho, mural)
            return discord.File(png, filename="resumo.png")
        except Exception:
            log.exception("Não consegui gerar o card do resumo")
            return None

    def embed_resumo(self, m: Maratona) -> discord.Embed:
        jogos = self.db.jogos(m.id)
        jogados = sorted((j for j in jogos if j.jogado), key=lambda j: j.ordem)
        sobras = [j for j in jogos if not j.jogado]
        mural = self.db.mural(m.id)
        notas = self.db.notas_da_maratona(m.id)

        def com_nota(j: Jogo) -> str:
            if j.id not in notas:
                return ""
            media, _ = notas[j.id]
            return f" · ⭐ {numero(media)}"

        e = discord.Embed(
            title=f"🏁 Fim da maratona: {m.nome}", color=COR_FIM,
            description=f"Foram **{plural(len(jogados), 'jogo', 'jogos')}** em "
                        f"**{duracao(m.criada_em, m.encerrada_em)}**. GG a todos! 🎉",
        )
        e.add_field(name="🎮 O que jogamos",
                    value=juntar([f"`{j.ordem}.` **{j.nome}** — <@{j.user_id}>{com_nota(j)}" for j in jogados],
                                 vazio="Nenhum jogo foi sorteado."), inline=False)
        if notas:
            melhor = max((j for j in jogados if j.id in notas), key=lambda j: (notas[j.id][0], notas[j.id][1]))
            e.add_field(name="🏆 Melhor da noite (até agora)",
                        value=f"**{melhor.nome}** — {resumo_nota(*notas[melhor.id])}", inline=False)
        if sobras:
            e.add_field(name="📦 Ficaram na roleta (revelados!)",
                        value=juntar([f"**{j.nome}** — <@{j.user_id}>" for j in sobras]), inline=False)
            e.set_footer(text="Os jogos que não saíram voltaram para a lista de quem colocou (/lista).")
        e.add_field(name="😤 Mural da Vergonha",
                    value=juntar(ranking(mural), vazio="Ninguém reclamou. Que grupo exemplar! 😇"), inline=False)
        return e

    # ---- notas dos jogos -------------------------------------------

    def embed_notas(self, jogo: Jogo, proximo: Optional[str] = None) -> discord.Embed:
        media, quantas = self.db.media_do_jogo(jogo.id)
        antes = f"Antes de começar **{proximo}**: " if proximo else ""
        e = discord.Embed(
            title=f"⭐ Que nota vocês dão para {jogo.nome}?", color=COR_ROLETA,
            description=f"{antes}foi o jogo **#{jogo.ordem}** da noite, escolhido por <@{jogo.user_id}>.\n"
                        "Cada um clica na sua nota, de 1 a 5. Mudou de ideia? É só clicar de novo.",
        )
        e.add_field(name="Nota do grupo", value=resumo_nota(media, quantas))
        e.set_thumbnail(url=url_nando(self.bot))
        e.set_footer(text="As notas formam o ranking dos jogos favoritos do grupo (/stats → 🏆).")
        return e

    async def pedir_notas(self, canal, jogo: Jogo, proximo: Optional[str] = None) -> None:
        """Posta os botões de nota de um jogo (uma vez só por jogo)."""
        if canal is None or not self.db.marcar_nota_pedida(jogo.id):
            return
        try:
            await canal.send(embed=self.embed_notas(jogo, proximo), view=view_notas(jogo.id),
                             allowed_mentions=SEM_PING)
        except discord.HTTPException as exc:
            log.warning("Não consegui pedir as notas de %s: %s", jogo.nome, exc)

    async def receber_nota(self, interaction: discord.Interaction, jogo_id: int, nota: int) -> None:
        self.db.dar_nota(jogo_id, interaction.user.id, nota)
        jogo = self.db.jogo(jogo_id)
        # Atualiza a média na mensagem (mantendo o "Antes de começar…" que já estava escrito).
        embed = self.embed_notas(jogo)
        if interaction.message and interaction.message.embeds:
            embed.description = interaction.message.embeds[0].description
        await interaction.response.edit_message(embed=embed, allowed_mentions=SEM_PING)
        await interaction.followup.send(f"Sua nota para **{jogo.nome}**: {estrelas(nota)} ({nota}). Valeu!",
                                        ephemeral=True)

    def embed_ranking(self, guild_id: int) -> discord.Embed:
        """Os jogos favoritos do grupo, pelas notas (botão 🏆 do /stats)."""
        jogos = ranking_notas(self.db.notas_do_servidor(guild_id))
        linhas = []
        for i, j in enumerate(jogos[:10]):
            posicao = MEDALHAS[i] if i < len(MEDALHAS) else f"`{i + 1}.`"
            vezes = f" · jogado {j.vezes}x" if j.vezes > 1 else ""
            linhas.append(f"{posicao} **{j.nome}** — {estrelas(j.media)} {numero(j.media)} "
                          f"({j.notas} notas{vezes})")
        e = discord.Embed(
            title="🏆 Jogos favoritos do grupo", color=COR_ROLETA,
            description=juntar(linhas, limite=4000, vazio=(
                "Ainda não tem jogo com notas suficientes. Depois de cada jogo da maratona, "
                "o bot pede uma nota de 1 a 5. Joguem e votem! 🎮")),
        )
        e.set_thumbnail(url=url_nando(self.bot))
        e.set_footer(text=f"Média das notas de todas as maratonas · só entram jogos com "
                          f"{MINIMO_NOTAS_RANKING} notas ou mais")
        return e

    def embed_mural_completo(self, guild_id: int) -> discord.Embed:
        """O Mural da Vergonha de todos os tempos, inteiro (botão 😤 do /stats)."""
        e = discord.Embed(title="😤 Mural da Vergonha (todos os tempos)", color=COR_VERGONHA,
                          description=juntar(ranking(self.db.stats_mural(guild_id)), limite=4000,
                                             vazio="Ninguém nunca reclamou. Grupo exemplar! 😇"))
        e.set_thumbnail(url=url_nando(self.bot, "surpreso"))
        e.set_footer(text=ASSINATURA)
        return e

    # ---- estatísticas do grupo --------------------------------------

    @app_commands.command(name="stats", description="Estatísticas do grupo: maratonas, horas, jogos e quem mais coloca jogo.")
    @app_commands.guild_only()
    async def cmd_stats(self, interaction: discord.Interaction) -> None:
        maratonas = self.db.stats_maratonas(interaction.guild_id)
        if not maratonas:
            raise ErroMaratona("Este servidor ainda não teve nenhuma maratona. Que tal abrir a primeira com "
                               "`/maratona`? 🎮")
        jogos = self.db.stats_jogos(interaction.guild_id)
        r = estatisticas.resumo(maratonas, jogos)

        e = discord.Embed(
            title="📊 Estatísticas do grupo", color=COR_DETALHE,
            description=(f"**{plural(r.maratonas, 'maratona', 'maratonas')}** · "
                         f"**{plural(r.jogos_jogados, 'jogo jogado', 'jogos jogados')}** · "
                         f"**{estatisticas.horas_texto(r.horas)}** de jogatina\n"
                         f"Desde {carimbo(r.desde, 'D')}"),
        )
        e.add_field(name="🎰 Mais sorteados",
                    value=juntar([f"**{nome}** — {vezes}x" for nome, vezes in estatisticas.mais_sorteados(jogos)],
                                 vazio="Nenhum jogo sorteado ainda."))
        e.add_field(name="🙋 Quem mais coloca jogo",
                    value=juntar([f"<@{p.user_id}> — {plural(p.colocou, 'jogo', 'jogos')} "
                                  f"({p.sairam} {'saiu' if p.sairam == 1 else 'saíram'})"
                                  for p in estatisticas.por_pessoa(jogos)]))
        favoritos = ranking_notas(self.db.notas_do_servidor(interaction.guild_id))
        if favoritos:
            f = favoritos[0]
            e.add_field(name="⭐ Favorito do grupo",
                        value=f"**{f.nome}** — {estrelas(f.media)} {numero(f.media)}\nVeja o resto no **🏆 Ranking dos jogos** aqui embaixo",
                        inline=False)
        e.add_field(name="😤 Mural da Vergonha (todos os tempos)",
                    value=juntar(ranking(self.db.stats_mural(interaction.guild_id)[:3]),
                                 vazio="Ninguém nunca reclamou. Grupo exemplar! 😇"), inline=False)
        meses = estatisticas.por_mes([c for c, _ in maratonas], datetime.now(timezone.utc))
        e.add_field(name="📅 Maratonas por mês", value=f"```\n{estatisticas.barras(meses)}\n```", inline=False)
        e.set_thumbnail(url=url_nando(self.bot))
        e.set_footer(text="As horas vão da abertura ao encerramento de cada maratona (no máximo 12h por noite).")
        await interaction.response.send_message(embed=e, view=StatsView(self), allowed_mentions=SEM_PING)

    # ---- /lista ----------------------------------------------------

    @app_commands.command(name="lista", description="Sua lista de jogos para as próximas maratonas (e a lista do grupo).")
    @app_commands.describe(jogo="Opcional: um jogo para guardar na lista (aparecem sugestões da Steam)")
    async def cmd_lista(self, interaction: discord.Interaction,
                        jogo: Optional[app_commands.Range[str, 1, 100]] = None) -> None:
        if jogo:
            await self.receber_nomes(interaction, [jogo], self.concluir_lista)
            return
        view = MinhaListaView(self, interaction.user.id)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    @cmd_lista.autocomplete("jogo")
    async def _autocompletar_lista(self, interaction: discord.Interaction, atual: str):
        return await autocompletar_jogo(self.bot, interaction, atual)

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

        e = discord.Embed(title="😤 Mural da Vergonha", description=texto, color=COR_VERGONHA)
        e.set_thumbnail(url=url_nando(self.bot, "surpreso"))
        e.set_footer(text=ASSINATURA)
        await interaction.response.send_message(embed=e, allowed_mentions=SEM_PING)
        await self.atualizar_painel(m.id)


class StatsView(discord.ui.View):
    """Botões do /stats: o ranking dos jogos favoritos e o Mural da Vergonha inteiro."""

    def __init__(self, cog: MaratonaCog) -> None:
        super().__init__(timeout=1800)
        self.cog = cog

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    @discord.ui.button(label="Ranking dos jogos", emoji="🏆", style=discord.ButtonStyle.primary)
    async def ranking_jogos(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=self.cog.embed_ranking(interaction.guild_id),
                                                allowed_mentions=SEM_PING)

    @discord.ui.button(label="Mural da Vergonha", emoji="😤", style=discord.ButtonStyle.secondary)
    async def mural(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await interaction.response.send_message(embed=self.cog.embed_mural_completo(interaction.guild_id),
                                                allowed_mentions=SEM_PING)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(MaratonaCog(bot))
