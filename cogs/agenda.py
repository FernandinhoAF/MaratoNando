"""Maratona agendada (/maratona → 📅 Agendar): marca a maratona para depois.

  • Posta uma mensagem com ✅ Eu vou · 🤔 Talvez · ❌ Não vou (e 🗑️ Cancelar para quem marcou).
  • Cria um evento no calendário do servidor (se o bot tiver a permissão "Gerenciar eventos").
  • 15 minutos antes, abre a maratona sozinho no canal, já com quem confirmou dentro,
    e marca o cargo de jogos e quem disse "Eu vou".

Os agendamentos ficam no banco e os botões guardam o número do agendamento no custom_id,
então tudo continua funcionando se o bot reiniciar no meio da semana.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord.ext import commands, tasks

import agenda
from cogs.maratona import SEM_PING, mencao_do_cargo, plural, tratar_erro
from db import Agendamento, Database, ErroMaratona
from marca import COR_PAINEL, url_nando

log = logging.getLogger("maratonando.agenda")

DURACAO_EVENTO = timedelta(hours=4)  # o calendário do Discord pede um horário de fim para eventos fora de call
BOTOES = {
    "vou": ("Eu vou", "✅", discord.ButtonStyle.success),
    "talvez": ("Talvez", "🤔", discord.ButtonStyle.secondary),
    "nao": ("Não vou", "❌", discord.ButtonStyle.secondary),
    "cancelar": ("Cancelar", "🗑️", discord.ButtonStyle.danger),
}


def iso_utc(momento: datetime) -> str:
    return momento.astimezone(timezone.utc).isoformat(timespec="seconds")


def carimbo(iso: str, estilo: str) -> str:
    """Data no formato do Discord: cada pessoa vê no próprio fuso (ex.: "sábado, 24 de outubro às 21:30")."""
    return f"<t:{int(datetime.fromisoformat(iso).timestamp())}:{estilo}>"


class BotaoAgenda(discord.ui.DynamicItem[discord.ui.Button],
                  template=r"agenda:(?P<id>\d+):(?P<acao>vou|talvez|nao|cancelar)"):
    def __init__(self, agendamento_id: int, acao: str) -> None:
        rotulo, emoji, estilo = BOTOES[acao]
        super().__init__(discord.ui.Button(label=rotulo, emoji=emoji, style=estilo,
                                           custom_id=f"agenda:{agendamento_id}:{acao}"))
        self.agendamento_id = agendamento_id
        self.acao = acao

    @classmethod
    async def from_custom_id(cls, interaction, item, match):
        return cls(int(match["id"]), match["acao"])

    async def callback(self, interaction: discord.Interaction) -> None:
        cog: "AgendaCog" = interaction.client.get_cog("AgendaCog")  # type: ignore[assignment]
        try:
            await cog.clicou(interaction, self.agendamento_id, self.acao)
        except Exception as erro:
            await tratar_erro(interaction, erro)


def view_agenda(agendamento_id: int) -> discord.ui.View:
    view = discord.ui.View(timeout=None)
    for acao in BOTOES:
        view.add_item(BotaoAgenda(agendamento_id, acao))
    return view


class AgendaCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        self.bot.add_dynamic_items(BotaoAgenda)
        self.despertador.start()

    async def cog_unload(self) -> None:
        self.despertador.cancel()

    # ---- mensagem ---------------------------------------------------

    def embed(self, ag: Agendamento) -> discord.Embed:
        r = self.db.respostas_agendamento(ag.id)
        titulos = {"marcado": "📅", "aberto": "▶️", "cancelado": "🗑️", "perdido": "⌛"}
        e = discord.Embed(title=f"{titulos.get(ag.status, '📅')} {ag.nome}",
                          color=COR_PAINEL if ag.status == "marcado" else discord.Color.dark_grey())
        quando = f"**{carimbo(ag.inicio, 'F')}** ({carimbo(ag.inicio, 'R')})"
        if ag.status == "marcado":
            e.description = (f"{quando}\nMarcada por <@{ag.host_id}>.\n\n"
                             f"{agenda.LEMBRETE_MINUTOS} minutos antes, o bot abre a maratona aqui e chama o pessoal "
                             "(o cargo de jogos e quem confirmou). Enquanto isso, vão separando os jogos com "
                             "`/lista`!")
        elif ag.status == "aberto":
            e.description = f"{quando}\n**Começou!** A maratona foi aberta aqui no canal. 🎮"
        elif ag.status == "cancelado":
            e.description = f"~~{quando}~~\n**Cancelada** por quem marcou."
        else:
            e.description = f"{quando}\nO bot estava desligado na hora e não conseguiu abrir a maratona. 😕"

        def nomes(ids: list[int]) -> str:
            return " ".join(f"<@{u}>" for u in ids) if ids else "—"

        e.add_field(name=f"✅ Vão ({len(r['vou'])})", value=nomes(r["vou"])[:1024], inline=False)
        e.add_field(name=f"🤔 Talvez ({len(r['talvez'])})", value=nomes(r["talvez"])[:1024])
        e.add_field(name=f"❌ Não vão ({len(r['nao'])})", value=nomes(r["nao"])[:1024])
        e.set_thumbnail(url=url_nando(self.bot))
        if ag.status == "marcado":
            e.set_footer(text="Clique para responder · quem marcou pode cancelar"
                         + (" · também está no calendário do servidor" if ag.evento_id else ""))
        return e

    def para_faxina(self, mensagem, guild_id) -> None:
        """Mensagens da maratona agendada também são da faxina (veja cogs/limpeza.py)."""
        limpeza = self.bot.get_cog("LimpezaCog")
        if limpeza is not None:
            limpeza.marcar_para_apagar(mensagem, guild_id)

    async def atualizar_mensagem(self, ag: Agendamento) -> None:
        if ag.message_id is None:
            return
        canal = self.bot.get_partial_messageable(ag.channel_id, guild_id=ag.guild_id)
        try:
            await canal.get_partial_message(ag.message_id).edit(
                embed=self.embed(ag), view=view_agenda(ag.id) if ag.status == "marcado" else None,
                allowed_mentions=SEM_PING)
        except discord.HTTPException as exc:
            log.warning("Não consegui atualizar o agendamento %s: %s", ag.id, exc)

    # ---- agendar (chamado pelo botão 📅 Agendar do /maratona) ---------

    async def agendar(self, interaction: discord.Interaction, dia: str, hora: str, nome: Optional[str],
                      call: Optional[discord.abc.GuildChannel]) -> None:
        try:
            momento = agenda.quando(dia, hora, datetime.now(timezone.utc))
        except agenda.ErroData as erro:
            raise ErroMaratona(str(erro)) from None
        nome = nome or "Maratona de Jogos"
        ag = self.db.criar_agendamento(interaction.guild_id, interaction.channel_id, interaction.user.id,
                                       nome, iso_utc(momento))
        self.db.responder_agendamento(ag.id, interaction.user.id, "vou")  # quem marcou, vai

        evento_id, aviso = await self.criar_evento(interaction, ag, momento, call)
        await interaction.response.send_message(embed=self.embed(self.db.agendamento(ag.id)),
                                                view=view_agenda(ag.id), allowed_mentions=SEM_PING)
        msg = await interaction.original_response()
        self.db.definir_mensagem_agendamento(ag.id, msg.id, evento_id)
        self.para_faxina(msg, interaction.guild_id)  # o convite só some depois que a maratona começa
        if evento_id:
            await self.atualizar_mensagem(self.db.agendamento(ag.id))  # rodapé com "está no calendário"
        if aviso:
            await interaction.followup.send(aviso, ephemeral=True)

    async def criar_evento(self, interaction: discord.Interaction, ag: Agendamento, momento: datetime,
                           call: Optional[discord.abc.GuildChannel]) -> tuple[Optional[int], str]:
        """Cria o evento no calendário do servidor. Devolve (id do evento ou None, aviso para quem marcou)."""
        descricao = (f"Marcada por {interaction.user.display_name} com o MaratoNando. "
                     f"Confirme presença em #{getattr(interaction.channel, 'name', 'canal do bot')}. "
                     "Caiu, jogou. Sem reclamar.")
        try:
            if call is not None:
                evento = await interaction.guild.create_scheduled_event(
                    name=ag.nome, start_time=momento, entity_type=discord.EntityType.voice, channel=call,
                    privacy_level=discord.PrivacyLevel.guild_only, description=descricao)
            else:
                evento = await interaction.guild.create_scheduled_event(
                    name=ag.nome, start_time=momento, end_time=momento + DURACAO_EVENTO,
                    entity_type=discord.EntityType.external, privacy_level=discord.PrivacyLevel.guild_only,
                    location=f"No Discord, em #{getattr(interaction.channel, 'name', 'canal do bot')}"[:100],
                    description=descricao)
            return evento.id, ""
        except discord.Forbidden:
            return None, ("Marquei a maratona, mas não consegui colocar no **calendário do servidor**: "
                          "o bot precisa da permissão **Gerenciar eventos**. O resto (botões e aviso antes de "
                          "começar) funciona normalmente.")
        except discord.HTTPException as exc:
            log.warning("Não consegui criar o evento do agendamento %s: %s", ag.id, exc)
            return None, "Marquei a maratona, mas o Discord não aceitou criar o evento no calendário agora."

    # ---- botões -----------------------------------------------------

    async def clicou(self, interaction: discord.Interaction, agendamento_id: int, acao: str) -> None:
        ag = self.db.agendamento(agendamento_id)
        if ag is None:
            raise ErroMaratona("Não achei esse agendamento.")
        if acao == "cancelar":
            perms = getattr(interaction.user, "guild_permissions", None)
            if interaction.user.id != ag.host_id and not (perms and perms.manage_guild):
                raise ErroMaratona(f"Só quem marcou (<@{ag.host_id}>) pode cancelar.")
            if not self.db.mudar_status_agendamento(ag.id, "cancelado"):
                raise ErroMaratona("Esse agendamento não está mais valendo.")
            await self.apagar_evento(ag)
            ag = self.db.agendamento(ag.id)
            await interaction.response.edit_message(embed=self.embed(ag), view=None, allowed_mentions=SEM_PING)
            return
        self.db.responder_agendamento(ag.id, interaction.user.id, acao)
        await interaction.response.edit_message(embed=self.embed(self.db.agendamento(ag.id)),
                                                view=view_agenda(ag.id), allowed_mentions=SEM_PING)

    async def apagar_evento(self, ag: Agendamento) -> None:
        guild = self.bot.get_guild(ag.guild_id)
        if guild is None or ag.evento_id is None:
            return
        try:
            evento = guild.get_scheduled_event(ag.evento_id) or await guild.fetch_scheduled_event(ag.evento_id)
            await evento.delete()
        except Exception as exc:  # o evento é só um extra: se falhar, fica registrado e segue a vida
            log.info("Não consegui apagar o evento %s: %s", ag.evento_id, exc)

    # ---- despertador: abre a maratona na hora ------------------------

    @tasks.loop(seconds=30)
    async def despertador(self) -> None:
        agora = datetime.now(timezone.utc)
        limite = iso_utc(agora + timedelta(minutes=agenda.LEMBRETE_MINUTOS))
        for ag in self.db.agendamentos_vencendo(limite):
            try:
                await self.abrir(ag, agora)
            except Exception:
                log.exception("Erro ao abrir o agendamento %s", ag.id)

    @despertador.before_loop
    async def antes_do_despertador(self) -> None:
        await self.bot.wait_until_ready()

    async def abrir(self, ag: Agendamento, agora: datetime) -> None:
        inicio = datetime.fromisoformat(ag.inicio)
        if agora > inicio + agenda.TOLERANCIA_ATRASO:
            # O bot estava desligado na hora: tarde demais para chamar todo mundo.
            if self.db.mudar_status_agendamento(ag.id, "perdido"):
                await self.atualizar_mensagem(self.db.agendamento(ag.id))
            return
        if not self.db.mudar_status_agendamento(ag.id, "aberto"):
            return  # outro ciclo já abriu

        canal = self.bot.get_channel(ag.channel_id)
        if canal is None:
            try:
                canal = await self.bot.fetch_channel(ag.channel_id)
            except discord.HTTPException:
                log.warning("Canal do agendamento %s sumiu", ag.id)
                return

        confirmados = self.db.respostas_agendamento(ag.id)["vou"]
        mencao, permissao, _ = mencao_do_cargo(self.db, ag.guild_id)
        pessoas = " ".join(f"<@{u}>" for u in confirmados)
        mencoes = permissao.merge(discord.AllowedMentions(users=[discord.Object(id=u) for u in confirmados]))
        quando = carimbo(ag.inicio, "R")
        maratona_cog = self.bot.get_cog("MaratonaCog")

        aberta = self.db.maratona_aberta(ag.guild_id)
        if aberta is None and maratona_cog is not None:
            m = self.db.criar_maratona(ag.guild_id, ag.channel_id, ag.host_id, ag.nome)
            for u in confirmados:
                self.db.entrar_sem_jogo(m.id, u)  # quem confirmou já entra (e sai da lista ao colocar jogo)
            texto = (f"{mencao}{pessoas}\n⏰ **{ag.nome}** começa {quando}! A maratona já está aberta: "
                     "coloquem seus jogos na roleta (**➕ Adicionar jogos**).")
            await maratona_cog.enviar_painel_no_canal(canal, m, texto, mencoes)
        else:
            onde = f" em <#{aberta.channel_id}>" if aberta else ""
            texto = (f"{mencao}{pessoas}\n⏰ **{ag.nome}** começa {quando}! "
                     f"Já tem uma maratona aberta{onde}, então não abri outra.")
            self.para_faxina(await canal.send(texto, allowed_mentions=mencoes), ag.guild_id)

        await self.atualizar_mensagem(self.db.agendamento(ag.id))
        await self.iniciar_evento(ag)

    async def iniciar_evento(self, ag: Agendamento) -> None:
        """Deixa o evento do calendário como "acontecendo agora" (se ainda existir)."""
        guild = self.bot.get_guild(ag.guild_id)
        if guild is None or ag.evento_id is None:
            return
        try:
            evento = guild.get_scheduled_event(ag.evento_id) or await guild.fetch_scheduled_event(ag.evento_id)
            if evento.status == discord.EventStatus.scheduled:
                await evento.start()
        except Exception as exc:  # o evento é só um extra: se falhar, fica registrado e segue a vida
            log.info("Não consegui iniciar o evento %s: %s", ag.evento_id, exc)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AgendaCog(bot))
