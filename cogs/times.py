"""/times: sorteia times para os jogos que são em equipe.

  • Com pessoas marcadas (jogadores: @Ana @Bia…), sorteia na hora.
  • Sem ninguém marcado, o bot monta a lista e pergunta se falta alguém antes de sortear,
    para quem chegou no meio não ficar de fora: começa com os participantes da maratona
    aberta; sem maratona, com quem está na sua call.
Quem chegou clica em "Vou jogar"; quem saiu clica em "Não vou". Quem organizou
também pode marcar/desmarcar pessoas. Depois é só clicar em "Sortear times".
"""

import re
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from cogs.maratona import SEM_PING, juntar, plural, tratar_erro
from marca import COR_DETALHE
from db import Database, ErroMaratona
from times import CORES, MAX_TIMES, dividir_em_times

COR_TIMES = COR_DETALHE  # ciano (Manual da Marca)


class TimesView(discord.ui.View):
    def __init__(self, dono_id: int, quantidade: int, jogadores: list[int], origem: str) -> None:
        super().__init__(timeout=3600)
        self.dono_id = dono_id
        self.quantidade = quantidade
        self.jogadores = list(dict.fromkeys(jogadores))
        self.origem = origem
        self.resultado: Optional[list[list[int]]] = None
        self.mensagem: Optional[discord.Message] = None
        self.montar()

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    async def on_timeout(self) -> None:
        if self.mensagem:
            try:
                await self.mensagem.edit(view=None)
            except discord.HTTPException:
                pass

    def pode_organizar(self, interaction: discord.Interaction) -> bool:
        perms = getattr(interaction.user, "guild_permissions", None)
        return interaction.user.id == self.dono_id or bool(perms and perms.manage_guild)

    def exigir_organizador(self, interaction: discord.Interaction) -> None:
        if not self.pode_organizar(interaction):
            raise ErroMaratona(f"Só <@{self.dono_id}> (quem pediu o sorteio) pode fazer isso.")

    # ---- montagem dos botões --------------------------------------

    def _botao(self, label, emoji, estilo, callback, linha=0) -> None:
        b = discord.ui.Button(label=label, emoji=emoji, style=estilo, row=linha)
        b.callback = callback
        self.add_item(b)

    def montar(self) -> None:
        self.clear_items()
        if self.resultado is None:
            self._botao("Vou jogar", "✋", discord.ButtonStyle.success, self.entrar)
            self._botao("Não vou", "🚪", discord.ButtonStyle.secondary, self.sair)
            self._botao("Sortear times", "🎲", discord.ButtonStyle.primary, self.sortear)
            marcar = discord.ui.UserSelect(placeholder="Organizador: marcar/desmarcar alguém…",
                                           min_values=1, max_values=10, row=1)
            marcar.callback = self.marcar
            self.add_item(marcar)
        else:
            self._botao("Sortear de novo", "🔁", discord.ButtonStyle.secondary, self.sortear)
            self._botao("Mudar jogadores", "✏️", discord.ButtonStyle.secondary, self.voltar)

    # ---- embeds ---------------------------------------------------

    def embed(self) -> discord.Embed:
        if self.resultado is None:
            e = discord.Embed(title="👥 Sorteio de times: quem vai jogar?", color=COR_TIMES, description=(
                f"Comecei a lista com {self.origem}. **Chegou agora e vai jogar? Clique em ✋ Vou jogar.** "
                f"Quando estiver todo mundo, <@{self.dono_id}> clica em **🎲 Sortear times**."))
            e.add_field(name=f"Jogadores ({len(self.jogadores)})",
                        value=juntar([f"<@{j}>" for j in self.jogadores], vazio="Ninguém ainda."), inline=False)
            e.add_field(name="Times", value=str(self.quantidade))
            return e

        e = discord.Embed(title="🎲 Times sorteados!", color=COR_TIMES,
                          description="Caiu, jogou. Sem reclamar do time! 🫡")
        for (emoji, nome), time in zip(CORES, self.resultado):
            e.add_field(name=f"{emoji} Time {nome}", value=juntar([f"<@{j}>" for j in time], vazio="—"))
        return e

    async def atualizar(self, interaction: discord.Interaction) -> None:
        self.montar()
        await interaction.response.edit_message(embed=self.embed(), view=self, allowed_mentions=SEM_PING)

    # ---- ações ----------------------------------------------------

    async def entrar(self, interaction: discord.Interaction) -> None:
        if interaction.user.id in self.jogadores:
            await interaction.response.send_message("Você já está na lista! 👍", ephemeral=True)
            return
        self.jogadores.append(interaction.user.id)
        await self.atualizar(interaction)

    async def sair(self, interaction: discord.Interaction) -> None:
        if interaction.user.id not in self.jogadores:
            await interaction.response.send_message("Você não estava na lista.", ephemeral=True)
            return
        self.jogadores.remove(interaction.user.id)
        await self.atualizar(interaction)

    async def marcar(self, interaction: discord.Interaction) -> None:
        self.exigir_organizador(interaction)
        menu: discord.ui.UserSelect = next(c for c in self.children if isinstance(c, discord.ui.UserSelect))
        for pessoa in menu.values:
            if pessoa.bot:
                continue
            if pessoa.id in self.jogadores:
                self.jogadores.remove(pessoa.id)
            else:
                self.jogadores.append(pessoa.id)
        await self.atualizar(interaction)

    async def sortear(self, interaction: discord.Interaction) -> None:
        self.exigir_organizador(interaction)
        if len(self.jogadores) < self.quantidade:
            raise ErroMaratona(f"Para {self.quantidade} times, precisa de pelo menos "
                               f"{self.quantidade} jogadores (tem {len(self.jogadores)}).")
        self.resultado = dividir_em_times(self.jogadores, self.quantidade)
        await self.atualizar(interaction)

    async def voltar(self, interaction: discord.Interaction) -> None:
        self.exigir_organizador(interaction)
        self.resultado = None
        await self.atualizar(interaction)


class TimesCog(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]

    async def cog_app_command_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    def lista_inicial(self, interaction: discord.Interaction, usar_maratona: bool = True) -> tuple[list[int], str]:
        # 1º: quem está na maratona (colocou jogo ou clicou em "Tô jogando").
        #     Tem prioridade sobre a call, porque às vezes tem gente na call só conversando.
        m = self.db.maratona_aberta(interaction.guild_id) if usar_maratona else None
        if m is not None:
            pessoas = self.db.jogadores(m.id)
            if pessoas:
                return pessoas, "quem está jogando a maratona"
        # 2º: sem maratona, usa quem está na call.
        voz = getattr(getattr(interaction.user, "voice", None), "channel", None)
        if voz is not None:
            pessoas = [p.id for p in voz.members if not p.bot]
            if pessoas:
                return pessoas, f"quem está na call **🔊 {voz.name}**"
        if not usar_maratona:
            return [], "ninguém (você não está numa call)"
        return [], "ninguém (não achei maratona aberta nem call)"

    @app_commands.command(name="times", description="Sorteia times: da maratona, da sua call ou de quem você marcar.")
    @app_commands.describe(
        quantidade="Quantos times (padrão: 2)",
        jogadores="Marque quem vai jogar (@Ana @Bia…) para sortear na hora. Vazio = maratona ou sua call",
    )
    @app_commands.guild_only()
    async def cmd_times(self, interaction: discord.Interaction,
                        quantidade: app_commands.Range[int, 2, MAX_TIMES] = 2,
                        jogadores: Optional[str] = None) -> None:
        await self.abrir(interaction, quantidade, jogadores)

    async def abrir(self, interaction: discord.Interaction, quantidade: int = 2, jogadores: Optional[str] = None) -> None:
        """Monta a lista de quem joga e sorteia os times.

        Com pessoas marcadas, sorteia na hora (dá para refazer ou mudar a lista pelos botões).
        Sem ninguém marcado, começa com quem está na maratona (ou, sem maratona, na sua call)
        e pergunta se falta alguém antes de sortear. Também é usado pelo menu "Mais…" do painel.
        """
        marcados = list(dict.fromkeys(int(i) for i in re.findall(r"<@!?(\d+)>", jogadores or "")))
        if jogadores and not marcados:
            raise ErroMaratona("Não achei ninguém marcado. Use @ para marcar as pessoas, tipo `@Ana @Bia @Caio`.")
        if marcados:
            if len(marcados) < quantidade:
                raise ErroMaratona(f"Para {quantidade} times, marque pelo menos {quantidade} pessoas.")
            view = TimesView(interaction.user.id, quantidade, marcados, "as pessoas marcadas")
            view.resultado = dividir_em_times(view.jogadores, quantidade)
            view.montar()
        else:
            lista, origem = self.lista_inicial(interaction)
            view = TimesView(interaction.user.id, quantidade, lista, origem)
        await interaction.response.send_message(embed=view.embed(), view=view, allowed_mentions=SEM_PING)
        view.mensagem = await interaction.original_response()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(TimesCog(bot))
