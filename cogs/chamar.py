"""/bora: chama o pessoal para jogar, marcando o cargo de jogos.

  • /bora [jogo] [pessoas] [hora] [marcar_cargo]  posta o chamado (marca o cargo de /config cargo-jogos,
    a não ser que marcar_cargo seja Não).
  • Cada um responde com um clique: ✋ Bora · ⏰ Daqui a pouco · ❌ Hoje não.
  • "pessoas" = vagas. Os primeiros que clicam Bora estão dentro; quando lota, o bot marca só quem está
    dentro ("Fechou!") e quem clica depois vai para a fila de espera. Se alguém de dentro sai, o primeiro
    da fila sobe e é avisado.
  • Com "hora" (ex.: 21h), o chamado mostra o horário e, na hora, o bot avisa quem está dentro.
  • Se a Steam estiver ligada, mostra quem dos confirmados tem o jogo e quanto custa para quem não tem.
    Sem jogo escolhido, sugere os jogos mais baratos para quem topou.

Os botões guardam o número do chamado no próprio custom_id e as respostas ficam no banco,
então continuam funcionando mesmo se o bot reiniciar.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

import agenda
from vagas import dividir, ganharam_vaga, posicao_na_fila

from marca import COR_PAINEL

from biblioteca import reais, resumo_grupo
from cogs.maratona import SEM_PING, autocompletar_jogo, mencao_do_cargo, tratar_erro
from nomes import ler_escolha
from db import Database, ErroMaratona, limpar_nome

log = logging.getLogger("maratonando.chamar")

COR_CHAMADO = COR_PAINEL  # magenta (Manual da Marca)
BOTOES = {
    "bora": ("Bora", "✋", discord.ButtonStyle.success),
    "depois": ("Daqui a pouco", "⏰", discord.ButtonStyle.secondary),
    "nao": ("Hoje não", "❌", discord.ButtonStyle.secondary),
    "fim": ("Encerrar", "🔒", discord.ButtonStyle.danger),
}


class BotaoChamado(discord.ui.DynamicItem[discord.ui.Button],
                   template=r"chamar:(?P<id>\d+):(?P<acao>bora|depois|nao|fim)"):
    def __init__(self, chamado_id: int, acao: str) -> None:
        rotulo, emoji, estilo = BOTOES[acao]
        super().__init__(discord.ui.Button(label=rotulo, emoji=emoji, style=estilo,
                                           custom_id=f"chamar:{chamado_id}:{acao}"))
        self.chamado_id = chamado_id
        self.acao = acao

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]), match["acao"])

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: "ChamarCog" = interaction.client.get_cog("ChamarCog")  # type: ignore[assignment]
        try:
            await cog.responder(interaction, self.chamado_id, self.acao)
        except Exception as erro:
            await tratar_erro(interaction, erro)


def view_chamado(chamado_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for acao in BOTOES:
        view.add_item(BotaoChamado(chamado_id, acao))
    return view


class ChamarCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]
        self.info_steam: dict[int, str] = {}  # chamado → texto "quem tem / mais baratos" (calculado em 2º plano)
        self.tarefas: set[asyncio.Task] = set()

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(BotaoChamado)
        self.despertador.start()

    async def cog_unload(self) -> None:
        self.despertador.cancel()

    def dentro_e_fila(self, chamado_id: int) -> tuple[list[int], list[int]]:
        c = self.db.chamado(chamado_id)
        return dividir(self.db.respostas_chamado(chamado_id)["bora"], c["pessoas"])

    async def cog_app_command_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    # ---- mensagem ---------------------------------------------------

    def embed(self, chamado_id: int) -> discord.Embed:
        c = self.db.chamado(chamado_id)
        r = self.db.respostas_chamado(chamado_id)
        titulo = f"🎮 {c['jogo']}" if c["jogo"] else "🎮 Bora jogar alguma coisa?"
        if c["encerrado"]:
            titulo = f"🔒 {titulo} (encerrado)"

        def linha(emoji: str, nome: str, ids: list[int], contagem: Optional[str] = None) -> str:
            return f"{emoji} **{nome} ({contagem or len(ids)})**: " + (" ".join(f"<@{u}>" for u in ids) if ids else "—")

        dentro, fila = dividir(r["bora"], c["pessoas"])
        partes = []
        if c["inicio"]:
            ts = int(datetime.fromisoformat(c["inicio"]).timestamp())
            partes.append(f"🕘 **Começa <t:{ts}:t>** (<t:{ts}:R>)\n")
        if c["pessoas"]:
            partes.append(linha("✋", "Dentro", dentro, f"{len(dentro)}/{c['pessoas']}"))
            if fila:
                partes.append(linha("⏳", "Fila de espera", fila))
        else:
            partes.append(linha("✋", "Bora", dentro))
        partes += [linha("⏰", "Daqui a pouco", r["depois"]), linha("❌", "Hoje não", r["nao"])]
        if c["pessoas"]:
            faltam = c["pessoas"] - len(dentro)
            partes.append(f"\n✅ **Fechou!** {len(dentro)}/{c['pessoas']} · quem clicar Bora agora entra na fila"
                          if faltam <= 0 else f"\nFaltam **{faltam}** para fechar {c['pessoas']} vagas")
        e = discord.Embed(title=titulo, description="\n".join(partes),
                          color=discord.Color.dark_grey() if c["encerrado"] else COR_CHAMADO)
        if chamado_id in self.info_steam:
            e.add_field(name="💰 Steam", value=self.info_steam[chamado_id][:1024], inline=False)
        e.set_footer(text="Clique para responder · quem chamou pode encerrar")
        return e

    async def calcular_steam(self, chamado_id: int) -> None:
        """Atualiza, em segundo plano, quem dos confirmados tem o jogo (ou os mais baratos para eles)."""
        steam = self.bot.get_cog("SteamCog")
        c = self.db.chamado(chamado_id)
        if steam is None or c is None or c["message_id"] is None:
            return
        bora, _ = self.dentro_e_fila(chamado_id)
        if c["jogo"]:
            info = await steam.quem_tem_para(bora, c["jogo"])
            texto = resumo_grupo(*info) if info else None
        else:
            opcoes = await steam.mais_baratos_para(bora)
            texto = None
            if opcoes:
                itens = []
                for o in opcoes:
                    custo = "todo mundo tem" if o.todos_tem else ("grátis" if o.app.gratis else reais(o.custo))
                    itens.append(f"**[{o.app.nome}]({o.app.url})** ({custo})")
                texto = "💡 Mais baratos para quem topou: " + " · ".join(itens)
        if texto is None or self.info_steam.get(chamado_id) == texto:
            return
        self.info_steam[chamado_id] = texto
        canal = self.bot.get_partial_messageable(c["channel_id"], guild_id=c["guild_id"])
        try:
            await canal.get_partial_message(c["message_id"]).edit(embed=self.embed(chamado_id),
                                                                   allowed_mentions=SEM_PING)
        except discord.HTTPException as exc:
            log.warning("Não consegui atualizar o chamado %s: %s", chamado_id, exc)

    def em_segundo_plano(self, coro) -> None:
        tarefa = asyncio.create_task(coro)
        self.tarefas.add(tarefa)
        tarefa.add_done_callback(self.tarefas.discard)

    # ---- comando ----------------------------------------------------

    @app_commands.command(name="bora", description="Chama o pessoal para jogar: vagas com fila, horário e cargo de jogos.")
    @app_commands.describe(jogo="Qual jogo (deixe vazio para \"bora jogar alguma coisa?\")",
                           pessoas="Vagas: quantas pessoas cabem (quem chegar depois vai para a fila)",
                           hora="Horário de Brasília, se não for agora (ex.: 21h ou 21:30)",
                           marcar_cargo="Marcar o cargo de jogos? (padrão: sim)")
    @app_commands.guild_only()
    async def cmd_bora(self, interaction: discord.Interaction,
                       jogo: Optional[app_commands.Range[str, 1, 100]] = None,
                       pessoas: Optional[app_commands.Range[int, 2, 20]] = None,
                       hora: Optional[app_commands.Range[str, 1, 10]] = None,
                       marcar_cargo: bool = True) -> None:
        await self.chamar(interaction, jogo, pessoas, hora, marcar_cargo)

    @staticmethod
    def horario(hora: str) -> datetime:
        """"21h" → hoje às 21h; se esse horário de hoje já passou, amanhã."""
        agora = datetime.now(timezone.utc)
        try:
            return agenda.quando("hoje", hora, agora)
        except agenda.ErroData as erro:
            if "já passou" not in str(erro):
                raise ErroMaratona(str(erro)) from None
        return agenda.quando("amanhã", hora, agora)

    async def chamar(self, interaction: discord.Interaction, jogo: Optional[str], pessoas: Optional[int],
                     hora: Optional[str], marcar_cargo: bool) -> None:
        inicio = self.horario(hora) if hora else None
        appid = None
        if jogo:
            jogo, appid = ler_escolha(jogo)
            jogo = limpar_nome(jogo)
            steam = self.bot.get_cog("SteamCog")
            if appid and steam is not None:  # escolhido do autocompletar: o bot já sabe qual jogo é
                steam.lembrar_nome(jogo, appid, jogo)
        chamado_id = self.db.criar_chamado(
            interaction.guild_id, interaction.channel_id, interaction.user.id, jogo or None, pessoas,
            inicio.astimezone(timezone.utc).isoformat(timespec="seconds") if inicio else None)
        if marcar_cargo:
            mencao, permissao, aviso = mencao_do_cargo(self.db, interaction.guild_id)
        else:
            mencao, permissao, aviso = "", SEM_PING, ""
        quem = getattr(interaction.user, "display_name", interaction.user.name)
        quando = f" às <t:{int(inicio.timestamp())}:t>" if inicio else ""
        texto = f"{mencao}📣 **{quem} quer jogar{f' {jogo}' if jogo else ''}{quando}!**"
        await interaction.response.send_message(texto, embed=self.embed(chamado_id), view=view_chamado(chamado_id),
                                                allowed_mentions=permissao)
        msg = await interaction.original_response()
        self.db.definir_mensagem_chamado(chamado_id, msg.id)
        if aviso:
            await interaction.followup.send(f"Chamado feito{aviso}.", ephemeral=True)
        self.em_segundo_plano(self.calcular_steam(chamado_id))

    @cmd_bora.autocomplete("jogo")
    async def _autocompletar_bora(self, interaction: discord.Interaction, atual: str):
        return await autocompletar_jogo(self.bot, interaction, atual)

    # ---- respostas --------------------------------------------------

    async def responder(self, interaction: discord.Interaction, chamado_id: int, acao: str) -> None:
        c = self.db.chamado(chamado_id)
        if c is None or c["encerrado"]:
            raise ErroMaratona("Esse chamado já foi encerrado.")

        if acao == "fim":
            perms = getattr(interaction.user, "guild_permissions", None)
            if interaction.user.id != c["autor_id"] and not (perms and perms.manage_guild):
                raise ErroMaratona(f"Só <@{c['autor_id']}> (quem chamou) pode encerrar.")
            self.db.encerrar_chamado(chamado_id)
            await interaction.response.edit_message(embed=self.embed(chamado_id), view=None,
                                                    allowed_mentions=SEM_PING)
            return

        antes, _ = self.dentro_e_fila(chamado_id)
        self.db.responder_chamado(chamado_id, interaction.user.id, acao)
        await interaction.response.edit_message(embed=self.embed(chamado_id), allowed_mentions=SEM_PING)
        bora, fila = self.dentro_e_fila(chamado_id)

        posicao = posicao_na_fila(fila, interaction.user.id)
        if posicao:
            await interaction.followup.send(
                f"Lotou! 😅 Você está na **fila de espera** (posição {posicao}). Se alguém sair, você entra "
                "e o bot te avisa.", ephemeral=True)
        subiram = ganharam_vaga(antes, bora, interaction.user.id)
        if subiram and c["fechou_em"]:
            await interaction.followup.send(
                " ".join(f"<@{u}>" for u in subiram) + " 🎟️ abriu uma vaga e você saiu da fila: **tá dentro!**",
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False,
                                                         users=[discord.Object(id=u) for u in subiram]),
            )
        if c["pessoas"] and len(bora) >= c["pessoas"] and self.db.marcar_chamado_fechado(chamado_id):
            # Fechou o grupo: marca (com notificação) só quem confirmou.
            jogo = f" de {c['jogo']}" if c["jogo"] else ""
            await interaction.followup.send(
                f"🎮 **Fechou o grupo{jogo}!** " + " ".join(f"<@{u}>" for u in bora) + ", entrem na call! 🎧",
                allowed_mentions=discord.AllowedMentions(everyone=False, roles=False,
                                                         users=[discord.Object(id=u) for u in bora]),
            )
        # Quem entrou ou saiu do "Bora" muda a conta da Steam: recalcula em segundo plano.
        self.em_segundo_plano(self.calcular_steam(chamado_id))


    # ---- "hora de jogar" (chamados com horário) -----------------------

    @tasks.loop(seconds=30)
    async def despertador(self) -> None:
        agora = datetime.now(timezone.utc)
        for c in self.db.chamados_para_lembrar(agora.isoformat(timespec="seconds")):
            if not self.db.marcar_chamado_lembrado(c["id"]):
                continue
            if agora - datetime.fromisoformat(c["inicio"]) > timedelta(hours=1):
                continue  # o bot estava desligado na hora: avisar muito depois só atrapalha
            dentro, _ = self.dentro_e_fila(c["id"])
            if not dentro:
                continue
            jogo = f" {c['jogo']}" if c["jogo"] else ""
            canal = self.bot.get_partial_messageable(c["channel_id"], guild_id=c["guild_id"])
            try:
                await canal.send(" ".join(f"<@{u}>" for u in dentro) + f"\n⏰ **Hora de jogar{jogo}!** Entrem na call! 🎧",
                                 allowed_mentions=discord.AllowedMentions(
                                     everyone=False, roles=False, users=[discord.Object(id=u) for u in dentro]))
            except discord.HTTPException as exc:
                log.warning("Não consegui avisar a hora do chamado %s: %s", c["id"], exc)

    @despertador.before_loop
    async def antes_do_despertador(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChamarCog(bot))
