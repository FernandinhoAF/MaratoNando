"""/chamar: chama o pessoal para jogar, marcando o cargo de jogos.

  • /chamar [jogo] [pessoas]  posta o chamado marcando o cargo (/config cargo-jogos).
  • Cada um responde com um clique: ✋ Bora · ⏰ Daqui a pouco · ❌ Hoje não.
  • Com "pessoas", quando o número de "Bora" bate, o bot marca só quem confirmou: "Fechou!".
  • Se a Steam estiver ligada, mostra quem dos confirmados tem o jogo e quanto custa para quem não tem.
    Sem jogo escolhido, sugere os jogos mais baratos para quem topou.

Os botões guardam o número do chamado no próprio custom_id e as respostas ficam no banco,
então continuam funcionando mesmo se o bot reiniciar.
"""

import asyncio
import logging
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

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

    async def cog_app_command_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    # ---- mensagem ---------------------------------------------------

    def embed(self, chamado_id: int) -> discord.Embed:
        c = self.db.chamado(chamado_id)
        r = self.db.respostas_chamado(chamado_id)
        titulo = f"🎮 {c['jogo']}" if c["jogo"] else "🎮 Bora jogar alguma coisa?"
        if c["encerrado"]:
            titulo = f"🔒 {titulo} (encerrado)"

        def linha(emoji: str, nome: str, ids: list[int]) -> str:
            return f"{emoji} **{nome} ({len(ids)})**: " + (" ".join(f"<@{u}>" for u in ids) if ids else "—")

        partes = [linha("✋", "Bora", r["bora"]), linha("⏰", "Daqui a pouco", r["depois"]),
                  linha("❌", "Hoje não", r["nao"])]
        if c["pessoas"]:
            faltam = c["pessoas"] - len(r["bora"])
            partes.append(f"\n✅ **Fechou!** {len(r['bora'])}/{c['pessoas']} pessoas" if faltam <= 0
                          else f"\nFaltam **{faltam}** para fechar {c['pessoas']} pessoas")
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
        bora = self.db.respostas_chamado(chamado_id)["bora"]
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

    @app_commands.command(name="chamar", description="Chama o pessoal para jogar (marca o cargo de jogos).")
    @app_commands.describe(jogo="Qual jogo (deixe vazio para \"bora jogar alguma coisa?\")",
                           pessoas="Quantas pessoas fecham o grupo (opcional)")
    @app_commands.guild_only()
    async def cmd_chamar(self, interaction: discord.Interaction,
                         jogo: Optional[app_commands.Range[str, 1, 100]] = None,
                         pessoas: Optional[app_commands.Range[int, 2, 20]] = None) -> None:
        appid = None
        if jogo:
            jogo, appid = ler_escolha(jogo)
            jogo = limpar_nome(jogo)
            steam = self.bot.get_cog("SteamCog")
            if appid and steam is not None:  # escolhido do autocompletar: o bot já sabe qual jogo é
                steam.lembrar_nome(jogo, appid, jogo)
        chamado_id = self.db.criar_chamado(interaction.guild_id, interaction.channel_id, interaction.user.id,
                                           jogo or None, pessoas)
        mencao, permissao, aviso = mencao_do_cargo(self.db, interaction.guild_id)
        quem = getattr(interaction.user, "display_name", interaction.user.name)
        texto = f"{mencao}📣 **{quem} quer jogar{f' {jogo}' if jogo else ''}!**"
        await interaction.response.send_message(texto, embed=self.embed(chamado_id), view=view_chamado(chamado_id),
                                                allowed_mentions=permissao)
        msg = await interaction.original_response()
        self.db.definir_mensagem_chamado(chamado_id, msg.id)
        if aviso:
            await interaction.followup.send(f"Chamado feito{aviso}.", ephemeral=True)
        self.em_segundo_plano(self.calcular_steam(chamado_id))

    @cmd_chamar.autocomplete("jogo")
    async def _autocompletar(self, interaction: discord.Interaction, atual: str):
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

        self.db.responder_chamado(chamado_id, interaction.user.id, acao)
        await interaction.response.edit_message(embed=self.embed(chamado_id), allowed_mentions=SEM_PING)

        bora = self.db.respostas_chamado(chamado_id)["bora"]
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


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ChamarCog(bot))
