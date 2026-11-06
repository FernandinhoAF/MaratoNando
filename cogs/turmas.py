"""🗳️ "Quem topa?": testa se o pessoal jogaria um jogo e monta a turma dele.

  • /topa jogo [quando]  (ou o botão 🗳️ Quem topa? da ficha do /jogo) posta a enquete.
  • Cada um responde 🙋 Topo · 🤔 Talvez · 🙅 Passo (dá para mudar de ideia). Quem topa entra na turma.
  • A enquete mostra do que se trata o jogo (descrição, gênero, nº de jogadores, modos, preço e
    avaliações, da Steam em português) e, com a chave da Steam, quem da turma já tem e quanto falta.
  • A turma é chamada (o bot marca só quem topou) quando:
      – alguém faz um /bora desse jogo;
      – alguém da turma clica em 📣 Chamar a turma (abre um /bora do jogo);
      – e, na maratona, o painel mostra os jogos com turma (Mais opções… → 🔥 Jogos com turma
        coloca o jogo na roleta); se ele sair no sorteio, a turma fecha.
  • Depois de chamada, a turma fecha (o jogo foi jogado). Não tem prazo: até jogar, ela continua aberta.

Os botões guardam o número da turma no custom_id e as respostas ficam no banco, então
continuam funcionando mesmo se o bot reiniciar.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import ficha
import turmas
from biblioteca import resumo_grupo
from jogadores import extrair_de_detalhes
from cogs.maratona import SEM_PING, autocompletar_jogo, texto_resultado, tratar_erro
from db import Database, ErroMaratona, Maratona, limpar_nome
from marca import COR_PAINEL
from nomes import ler_escolha, valor_escolha

log = logging.getLogger("maratonando.turmas")

ESTILOS = {"topo": discord.ButtonStyle.success, "talvez": discord.ButtonStyle.secondary,
           "passo": discord.ButtonStyle.secondary}
FECHADA_POR = {"bora": "A turma foi chamada num /bora. Bom jogo! 🎮",
               "maratona": "Saiu na roleta da maratona. Bom jogo! 🎮"}


class BotaoTurma(discord.ui.DynamicItem[discord.ui.Button],
                 template=r"turma:(?P<id>\d+):(?P<acao>topo|talvez|passo|chamar)"):
    def __init__(self, turma_id: int, acao: str) -> None:
        if acao == "chamar":
            botao = discord.ui.Button(label="Chamar a turma", emoji="📣", style=discord.ButtonStyle.primary,
                                      custom_id=f"turma:{turma_id}:chamar")
        else:
            rotulo, emoji, _ = turmas.RESPOSTAS[acao]
            botao = discord.ui.Button(label=rotulo, emoji=emoji, style=ESTILOS[acao],
                                      custom_id=f"turma:{turma_id}:{acao}")
        super().__init__(botao)
        self.turma_id, self.acao = turma_id, acao

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]), match["acao"])

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: "TurmasCog" = interaction.client.get_cog("TurmasCog")  # type: ignore[assignment]
        try:
            await cog.clicou(interaction, self.turma_id, self.acao)
        except Exception as erro:
            await tratar_erro(interaction, erro)


def view_turma(turma_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for acao in (*turmas.RESPOSTAS, "chamar"):
        view.add_item(BotaoTurma(turma_id, acao))
    return view


class QuandoModal(discord.ui.Modal, title="Quem topa?"):
    """Botão 🗳️ da ficha do /jogo: pergunta (opcionalmente) quando seria."""

    quando = discord.ui.TextInput(label="Quando seria? (opcional)", placeholder="ex.: sábado à noite",
                                  required=False, max_length=60)

    def __init__(self, cog: "TurmasCog", nome: str, appid: Optional[int]) -> None:
        super().__init__(title=f"Quem topa {nome}?"[:45])
        self.cog, self.nome, self.appid = cog, nome, appid

    async def on_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.cog.perguntar(interaction, self.nome, self.appid, self.quando.value.strip() or None)


class ChamarTurmaModal(discord.ui.Modal, title="Chamar a turma"):
    """Botão 📣 da enquete: vira um /bora do jogo (que marca a turma)."""

    pessoas = discord.ui.TextInput(label="Vagas (opcional)", placeholder="ex.: 4 (vazio = sem limite)",
                                   required=False, max_length=2)
    hora = discord.ui.TextInput(label="Horário (opcional)", placeholder="ex.: 21h (vazio = agora)",
                                required=False, max_length=10)

    def __init__(self, cog: "TurmasCog", turma_id: int, nome: str) -> None:
        super().__init__(title=f"Chamar a turma de {nome}"[:45])
        self.cog, self.turma_id = cog, turma_id

    async def on_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        pessoas = None
        if self.pessoas.value.strip():
            try:
                pessoas = int(self.pessoas.value)
            except ValueError:
                raise ErroMaratona("Vagas: escreva só um número (ex.: 4), ou deixe vazio.") from None
            if not 2 <= pessoas <= 20:
                raise ErroMaratona("Vagas: de 2 a 20.")
        await self.cog.chamar_pelo_botao(interaction, self.turma_id, pessoas, self.hora.value.strip() or None)


class TurmaParaRoletaSelect(discord.ui.Select):
    def __init__(self, cog: "TurmasCog", maratona_id: int, abertas) -> None:
        opcoes = [discord.SelectOption(label=t["nome"][:100], value=str(t["id"]), emoji="🔥",
                                       description=f"{n} {'topa' if n == 1 else 'topam'}")
                  for t, n in abertas[:25]]
        super().__init__(placeholder="Qual jogo vai para a roleta?", options=opcoes)
        self.cog, self.maratona_id = cog, maratona_id

    async def callback(self, interaction: discord.Interaction) -> None:
        try:
            await self.cog.colocar_na_roleta(interaction, self.maratona_id, int(self.values[0]))
        except Exception as erro:
            await tratar_erro(interaction, erro)


class TurmasCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]
        self.info_steam: dict[int, str] = {}  # turma → "quem tem / quanto falta" (calculado em 2º plano)
        self.tarefas: set[asyncio.Task] = set()

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(BotaoTurma)

    async def cog_app_command_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    def em_segundo_plano(self, coro) -> None:
        tarefa = asyncio.create_task(coro)
        self.tarefas.add(tarefa)
        tarefa.add_done_callback(self.tarefas.discard)

    # ---- a enquete ---------------------------------------------------

    def embed(self, turma_id: int) -> discord.Embed:
        t = self.db.turma(turma_id)
        r = self.db.respostas_turma(turma_id)
        aberta = t["fechada_em"] is None
        # Primeiro do que se trata o jogo (para quem não conhece decidir), depois as respostas.
        partes = [f"<@{t['autor_id']}> quer saber quem topa jogar **{t['nome']}**."]
        if t["quando"]:
            partes.append(f"🗓️ **Quando:** {t['quando']}")
        if t["descricao"]:
            partes.append(f"\n> {t['descricao']}")
        if t["ficha"]:
            partes.append("\n" + t["ficha"])
        if not aberta:
            partes.append(f"\n✅ {FECHADA_POR.get(t['fechada_por'], 'Essa turma já jogou.')}")
        e = discord.Embed(title=f"🗳️ Quem topa {t['nome']}?" if aberta else f"✅ {t['nome']}: turma chamada",
                          description="\n".join(partes)[:4096],
                          color=COR_PAINEL if aberta else discord.Color.dark_grey())
        if t["appid"]:
            e.url = f"https://store.steampowered.com/app/{t['appid']}/"
            e.set_image(url=turmas.url_capa(t["appid"]))
        e.add_field(name="🗳️ Respostas", value="\n".join(turmas.linhas_respostas(r))[:1024], inline=False)
        if aberta and turma_id in self.info_steam:
            e.add_field(name="💰 Para quem topou", value=self.info_steam[turma_id][:1024], inline=False)
        e.set_footer(text=("Quem clica 🙋 Topo entra na turma: num /bora desse jogo (ou no 📣), o bot marca "
                           "vocês. Só quem topou é marcado." if aberta else "Para perguntar de novo: /topa"))
        return e

    async def buscar_ficha(self, turma_id: int) -> None:
        """Busca na Steam (uma vez por turma) a descrição e as informações do jogo, e põe na enquete."""
        steam = self.bot.get_cog("SteamCog")
        t = self.db.turma(turma_id)
        if steam is None or t is None or not t["appid"] or t["descricao"] or t["ficha"]:
            return
        try:
            detalhes = await steam.loja.detalhes(t["appid"], idioma="brazilian")
            if not detalhes:
                return
            avaliacoes = await steam.loja.avaliacoes(t["appid"])  # None se a Steam não mandar agora
        except Exception as exc:  # informação extra: sem ela, a enquete continua funcionando
            log.info("Sem a ficha de %s para a turma %s: %s", t["appid"], turma_id, exc)
            return
        linha = self.db.jogadores_de([t["appid"]]).get(t["appid"])
        faixa = (linha["minimo"], linha["maximo"]) if linha is not None and linha["maximo"] else None
        faixa = faixa or extrair_de_detalhes(detalhes)
        self.db.definir_ficha_turma(turma_id, ficha.descricao_curta(detalhes) or None,
                                    "\n".join(turmas.linhas_sobre(detalhes, avaliacoes, faixa)))
        await self.atualizar_mensagem(turma_id)

    async def preparar(self, turma_id: int) -> None:
        """Depois de postar a enquete: primeiro a ficha do jogo, depois quem tem / quanto falta."""
        await self.buscar_ficha(turma_id)
        await self.calcular_steam(turma_id)

    async def calcular_steam(self, turma_id: int) -> None:
        """Atualiza, em segundo plano, quem da turma já tem o jogo e quanto falta comprar."""
        steam = self.bot.get_cog("SteamCog")
        t = self.db.turma(turma_id)
        if steam is None or t is None or t["fechada_em"] or t["message_id"] is None:
            return
        topam = self.db.respostas_turma(turma_id)["topo"]
        info = await steam.quem_tem_para(topam, t["nome"], t["appid"])
        texto = resumo_grupo(*info) if info else None
        if texto is None or self.info_steam.get(turma_id) == texto:
            return
        self.info_steam[turma_id] = texto
        await self.atualizar_mensagem(turma_id)

    async def atualizar_mensagem(self, turma_id: int, sem_botoes: bool = False) -> None:
        t = self.db.turma(turma_id)
        if t is None or t["message_id"] is None:
            return
        canal = self.bot.get_partial_messageable(t["channel_id"], guild_id=t["guild_id"])
        try:
            kwargs = {"view": None} if sem_botoes else {}
            await canal.get_partial_message(t["message_id"]).edit(embed=self.embed(turma_id),
                                                                   allowed_mentions=SEM_PING, **kwargs)
        except discord.HTTPException as exc:
            log.warning("Não consegui atualizar a enquete da turma %s: %s", turma_id, exc)

    async def perguntar(self, interaction: discord.Interaction, nome: str, appid: Optional[int],
                        quando: Optional[str]) -> None:
        """Posta a enquete (se o jogo já tem turma aberta, posta de novo a mesma, com os votos)."""
        nome = limpar_nome(nome)
        if not nome:
            raise ErroMaratona("Qual jogo? Escreva o nome.")
        turma_id, nova = self.db.abrir_turma(interaction.guild_id, nome, appid, interaction.user.id, quando)
        nome = self.db.turma(turma_id)["nome"]
        quem = getattr(interaction.user, "display_name", interaction.user.name)
        texto = (f"🗳️ **{quem} quer saber: quem topa jogar {nome}?**" if nova else
                 f"🗳️ **{quem} perguntou de novo: quem topa jogar {nome}?** (a turma continua com os votos de antes)")
        await interaction.response.send_message(texto, embed=self.embed(turma_id), view=view_turma(turma_id),
                                                allowed_mentions=SEM_PING)
        msg = await interaction.original_response()
        self.db.definir_mensagem_turma(turma_id, msg.channel.id, msg.id)
        self.em_segundo_plano(self.preparar(turma_id))

    @app_commands.command(name="topa", description="Pergunta quem topa jogar um jogo e monta a turma dele.")
    @app_commands.describe(jogo="Qual jogo (aparecem sugestões da Steam enquanto digita)",
                           quando="Opcional: quando seria (ex.: sábado à noite)")
    @app_commands.guild_only()
    async def cmd_topa(self, interaction: discord.Interaction, jogo: app_commands.Range[str, 1, 100],
                       quando: Optional[app_commands.Range[str, 1, 60]] = None) -> None:
        nome, appid = ler_escolha(jogo)
        steam = self.bot.get_cog("SteamCog")
        if steam is not None:
            try:
                if appid:  # escolhido do autocompletar: o bot já sabe qual jogo é
                    steam.lembrar_nome(nome, appid, nome)
                else:      # digitado: tenta reconhecer (sem achar, vale o nome digitado)
                    achado = await asyncio.wait_for(steam.reconhecer(nome), timeout=2.5)
                    if achado:
                        appid, nome = achado
            except Exception as exc:  # Steam fora do ar ou lenta: a enquete sai sem ela
                log.info("Não reconheci %r na Steam: %s", nome, exc)
        await self.perguntar(interaction, nome, appid, quando)

    @cmd_topa.autocomplete("jogo")
    async def _autocompletar_topa(self, interaction: discord.Interaction, atual: str):
        return await autocompletar_jogo(self.bot, interaction, atual)

    async def clicou(self, interaction: discord.Interaction, turma_id: int, acao: str) -> None:
        t = self.db.turma(turma_id)
        if t is None:
            raise ErroMaratona("Essa enquete não existe mais.")
        if t["fechada_em"]:
            raise ErroMaratona(f"Essa turma já jogou **{t['nome']}**. ✅ Para perguntar de novo: `/topa`.")
        if acao == "chamar":
            perms = getattr(interaction.user, "guild_permissions", None)
            pode = (interaction.user.id == t["autor_id"] or self.db.resposta_turma(turma_id, interaction.user.id) == "topo"
                    or (perms and perms.manage_guild))
            if not pode:
                raise ErroMaratona("Quem chama a turma é quem perguntou ou quem topou. Clique em 🙋 **Topo** primeiro!")
            await interaction.response.send_modal(ChamarTurmaModal(self, turma_id, t["nome"]))
            return
        self.db.responder_turma(turma_id, interaction.user.id, acao)
        await interaction.response.edit_message(embed=self.embed(turma_id), allowed_mentions=SEM_PING)
        if acao == "topo":
            await interaction.followup.send(
                f"🙋 Você está na turma de **{t['nome']}**. Quando alguém chamar (num `/bora` desse jogo ou no "
                "📣 da enquete), o bot te marca.", ephemeral=True)
        self.em_segundo_plano(self.calcular_steam(turma_id))

    # ---- chamar a turma ------------------------------------------------

    async def chamar_pelo_botao(self, interaction: discord.Interaction, turma_id: int,
                                pessoas: Optional[int], hora: Optional[str]) -> None:
        t = self.db.turma(turma_id)
        if t is None or t["fechada_em"]:
            raise ErroMaratona("Essa turma já foi chamada. ✅")
        chamar = self.bot.get_cog("ChamarCog")
        if chamar is None:
            raise ErroMaratona("A parte do /bora não está ligada agora.")
        jogo = valor_escolha(t["appid"], t["nome"]) if t["appid"] else t["nome"]
        # A turma é marcada lá dentro (o /bora procura a turma do jogo); o cargo de jogos não.
        await chamar.chamar(interaction, jogo, pessoas, hora, marcar_cargo=False)

    async def chamar_turma(self, interaction: discord.Interaction, nome: str, appid: Optional[int]) -> None:
        """Chamada pelo /bora depois de postar o chamado: marca quem topou e fecha a turma."""
        t = self.db.turma_do_jogo(interaction.guild_id, nome, appid)
        if t is None:
            return
        ids = turmas.para_marcar(self.db.respostas_turma(t["id"]), tirar=interaction.user.id)
        if not self.db.fechar_turma(t["id"], "bora"):
            return
        if ids:
            await interaction.followup.send(
                turmas.texto_chamada(t["nome"], ids),
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False,
                                                         users=[discord.Object(id=u) for u in ids]))
        await self.atualizar_mensagem(t["id"], sem_botoes=True)

    async def fechar_por_jogo(self, guild_id: int, nome: str, appid: Optional[int], motivo: str) -> None:
        """O jogo foi jogado (ex.: saiu na roleta da maratona): fecha a turma dele, sem marcar ninguém."""
        t = self.db.turma_do_jogo(guild_id, nome, appid)
        if t is not None and self.db.fechar_turma(t["id"], motivo):
            await self.atualizar_mensagem(t["id"], sem_botoes=True)

    # ---- na maratona ---------------------------------------------------

    def resumo_painel(self, guild_id: int) -> Optional[str]:
        return turmas.resumo_painel([(t["nome"], n) for t, n in self.db.turmas_abertas(guild_id)])

    async def mostrar_para_roleta(self, interaction: discord.Interaction, m: Maratona) -> None:
        """Menu "Mais opções…" → 🔥 Jogos com turma: escolhe um para colocar na roleta."""
        abertas = self.db.turmas_abertas(interaction.guild_id)
        if not abertas:
            raise ErroMaratona("Nenhum jogo tem turma agora. Para montar uma: `/topa jogo:…` "
                               "(ou o botão 🗳️ da ficha do `/jogo`).")
        linhas = [f"🔥 **{t['nome']}** — {n} {'topa' if n == 1 else 'topam'}" for t, n in abertas[:25]]
        view = discord.ui.View(timeout=600)
        view.add_item(TurmaParaRoletaSelect(self, m.id, abertas))
        await interaction.response.send_message(
            "**Jogos com turma** (quem topou numa enquete do `/topa`):\n" + "\n".join(linhas) +
            "\n\nEscolha um para colocar na roleta (conta como um jogo seu). Se ele sair no sorteio, "
            "a turma fecha.", view=view, ephemeral=True, allowed_mentions=SEM_PING)

    async def colocar_na_roleta(self, interaction: discord.Interaction, maratona_id: int, turma_id: int) -> None:
        m = self.db.maratona(maratona_id)
        t = self.db.turma(turma_id)
        if m is None or not m.aberta:
            raise ErroMaratona("Essa maratona já terminou. 🏁")
        if t is None or t["fechada_em"]:
            raise ErroMaratona("Essa turma já jogou. ✅")
        adicionados, recusados = self.db.adicionar_jogos(m.id, interaction.user.id, [t["nome"]],
                                                         {t["nome"]: t["appid"]})
        await interaction.response.send_message(texto_resultado(adicionados, recusados, "Na roleta"), ephemeral=True)
        maratona = self.bot.get_cog("MaratonaCog")
        if adicionados and maratona is not None:
            await maratona.atualizar_painel(m.id)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TurmasCog(bot))
