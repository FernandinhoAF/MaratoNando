"""Limpeza do chat: o bot apaga as próprias mensagens fora do canal dele.

Como funciona:
  • Um admin escolhe o canal do bot com /config canal-do-bot.
  • Lá, as mensagens do bot ficam para sempre.
  • Em qualquer outro canal, cada mensagem pública do bot é agendada para sumir
    depois de alguns minutos (padrão: 10, muda com /config apagar-depois).
  • Se alguém clicar num botão da mensagem (ex.: lista do /times), o prazo recomeça,
    para nada sumir no meio do uso.
  • O painel de uma maratona aberta nunca some enquanto ela estiver rolando.

Não precisa mexer em cada comando: o Discord avisa o bot (evento on_message)
de toda mensagem que ele mesmo manda, e é aí que o agendamento acontece.
A agenda fica no banco, então sobrevive se o bot reiniciar.
"""

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands

from marca import COR_DETALHE
from discord.ext import commands, tasks

from cogs.maratona import tratar_erro
from db import Database

log = logging.getLogger("maratonando.limpeza")

COR_CONFIG = COR_DETALHE  # ciano (Manual da Marca)


def agora() -> datetime:
    return datetime.now(timezone.utc)


class LimpezaCog(commands.Cog):
    config = app_commands.Group(
        name="config",
        description="Configurações do MaratoNando neste servidor.",
        guild_only=True,
        default_permissions=discord.Permissions(manage_guild=True),  # só admins veem
    )

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]

    async def cog_load(self) -> None:
        self.faxina.start()

    async def cog_unload(self) -> None:
        self.faxina.cancel()

    async def cog_app_command_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    # ---- regras ----------------------------------------------------

    def deve_apagar(self, mensagem: discord.Message) -> Optional[int]:
        """Quantos minutos até apagar esta mensagem, ou None se ela deve ficar."""
        if mensagem.guild is None or mensagem.flags.ephemeral:
            return None
        canal_bot, minutos = self.db.limpeza_config(mensagem.guild.id)
        if canal_bot is None or minutos == 0:
            return None
        canal = mensagem.channel
        if canal.id == canal_bot or getattr(canal, "parent_id", None) == canal_bot:  # tópicos do canal do bot
            return None
        return minutos

    # ---- eventos ---------------------------------------------------

    @commands.Cog.listener()
    async def on_message(self, mensagem: discord.Message) -> None:
        if self.bot.user is None or mensagem.author.id != self.bot.user.id:
            return
        minutos = self.deve_apagar(mensagem)
        if minutos is not None:
            self.db.agendar_apagar(mensagem.id, mensagem.channel.id, mensagem.guild.id,
                                   agora() + timedelta(minutes=minutos))

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        """Alguém clicou num botão/menu de uma mensagem do bot: o prazo dela recomeça."""
        if interaction.message is None or interaction.guild_id is None:
            return
        _, minutos = self.db.limpeza_config(interaction.guild_id)
        if minutos:
            self.db.adiar_apagar(interaction.message.id, agora() + timedelta(minutes=minutos))

    @tasks.loop(seconds=30)
    async def faxina(self) -> None:
        for message_id, channel_id, guild_id in self.db.mensagens_vencidas(agora()):
            # O painel de uma maratona que ainda está rolando não pode sumir.
            m = self.db.maratona_por_painel(message_id)
            if m is not None and m.aberta:
                _, minutos = self.db.limpeza_config(guild_id)
                self.db.adiar_apagar(message_id, agora() + timedelta(minutes=minutos or 10))
                continue
            canal = self.bot.get_partial_messageable(channel_id, guild_id=guild_id)
            try:
                await canal.get_partial_message(message_id).delete()
            except (discord.NotFound, discord.Forbidden):
                pass  # já foi apagada por alguém, ou o bot perdeu acesso ao canal
            except discord.HTTPException as exc:
                log.warning("Não consegui apagar a mensagem %s: %s", message_id, exc)
            self.db.desagendar(message_id)

    @faxina.before_loop
    async def antes_da_faxina(self) -> None:
        await self.bot.wait_until_ready()

    # ---- /config ---------------------------------------------------

    def embed_config(self, guild_id: int) -> discord.Embed:
        canal_bot, minutos = self.db.limpeza_config(guild_id)
        e = discord.Embed(title="⚙️ Configurações do MaratoNando", color=COR_CONFIG)
        e.add_field(name="Canal do bot", value=f"<#{canal_bot}>" if canal_bot else "Nenhum (nada é apagado)")
        e.add_field(name="Apagar fora dele depois de",
                    value="Nunca" if minutos == 0 else f"{minutos} min")
        cargo = self.db.cargo_jogos(guild_id)
        e.add_field(name="Cargo de jogos", value=f"<@&{cargo}>" if cargo else "Nenhum")
        e.add_field(name="Avisos de promoção", value=(
            "Ligados" if self.db.avisar_promocoes(guild_id) and canal_bot
            else "Ligados, mas precisam do canal do bot" if self.db.avisar_promocoes(guild_id) else "Desligados"))
        if canal_bot and minutos:
            e.description = (f"Em <#{canal_bot}> as mensagens do bot ficam. Em qualquer outro canal, elas somem "
                             f"{minutos} min depois da última vez que alguém mexeu nelas. "
                             "O painel de uma maratona aberta só some depois que ela acaba.")
        return e

    @config.command(name="canal-do-bot", description="Escolhe o canal do bot (lá as mensagens não são apagadas).")
    @app_commands.describe(canal="Canal do bot (deixe vazio para desligar a limpeza)")
    async def cmd_canal(self, interaction: discord.Interaction, canal: Optional[discord.TextChannel] = None) -> None:
        self.db.definir_canal_bot(interaction.guild_id, canal.id if canal else None)
        texto = (f"✅ {canal.mention} agora é o canal do MaratoNando." if canal
                 else "🧹 Limpeza desligada: o bot não apaga mais nenhuma mensagem.")
        await interaction.response.send_message(texto, embed=self.embed_config(interaction.guild_id), ephemeral=True)

    @config.command(name="apagar-depois", description="Quanto tempo as mensagens do bot ficam fora do canal dele.")
    @app_commands.describe(minutos="Minutos até apagar (0 = nunca apagar)")
    async def cmd_minutos(self, interaction: discord.Interaction,
                          minutos: app_commands.Range[int, 0, 1440]) -> None:
        self.db.definir_minutos_limpeza(interaction.guild_id, minutos)
        await interaction.response.send_message(embed=self.embed_config(interaction.guild_id), ephemeral=True)

    @config.command(name="promocoes",
                    description="Liga/desliga os avisos de promoção dos jogos das listas (vão para o canal do bot).")
    @app_commands.describe(ativo="Avisar quando um jogo das listas entrar em promoção na Steam?")
    async def cmd_promocoes(self, interaction: discord.Interaction, ativo: bool) -> None:
        self.db.definir_avisar_promocoes(interaction.guild_id, ativo)
        await interaction.response.send_message(embed=self.embed_config(interaction.guild_id), ephemeral=True)

    @config.command(name="cargo-jogos",
                    description="Cargo marcado quando abre maratona e no /chamar (vazio = não marcar ninguém).")
    @app_commands.describe(cargo="O cargo que vocês usam para chamar o pessoal para jogar")
    async def cmd_cargo(self, interaction: discord.Interaction, cargo: Optional[discord.Role] = None) -> None:
        self.db.definir_cargo_jogos(interaction.guild_id, cargo.id if cargo else None)
        aviso = ""
        eu = interaction.guild.me if interaction.guild else None
        if cargo and not cargo.mentionable and eu and not eu.guild_permissions.mention_everyone:
            aviso = ("\n\n⚠️ Esse cargo não está como **mencionável** e eu não tenho a permissão "
                     "**Mencionar @everyone, @here e todos os cargos**. Sem uma das duas, a marcação aparece "
                     "mas ninguém é notificado. Para resolver: Configurações do servidor → Cargos → "
                     f"{cargo.name} → ligue **Permitir que todos @mencionem este cargo**.")
        await interaction.response.send_message(
            (f"✅ {cargo.mention} vai ser marcado quando abrirem uma maratona e no `/chamar`." if cargo
             else "🔕 Nenhum cargo vai ser marcado.") + aviso,
            embed=self.embed_config(interaction.guild_id), ephemeral=True)

    @config.command(name="ver", description="Mostra as configurações atuais.")
    async def cmd_ver(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(embed=self.embed_config(interaction.guild_id), ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LimpezaCog(bot))
