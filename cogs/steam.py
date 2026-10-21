"""Contas da Steam e o /maratona baratos.

  • /steam vincular perfil:<link> [pessoa:@alguém]  liga a conta da Steam de alguém ao Discord.
    Qualquer um vincula a própria conta; quem tem "Gerenciar servidor" pode vincular a dos outros.
  • /steam desvincular [pessoa]  e  /steam ver.
  • /maratona baratos (o comando fica no cogs/maratona.py e chama mostrar_baratos daqui):
    lê as bibliotecas de quem está jogando a maratona, descobre quais jogos são multiplayer
    e quanto custam, e mostra o ranking do mais barato para o grupo.
"""

import asyncio
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import aiohttp
import discord
from discord import app_commands
from discord.ext import commands

from biblioteca import (AppSteam, Opcao, app_da_linha, app_de_detalhes, calcular_opcoes,
                        escolher_candidatos, ler_perfil, reais)
from nomes import ranquear_parecidos, termos_de_busca
from radar import normalizar
from cogs.maratona import SEM_PING, juntar, plural, tratar_erro
from marca import COR_DETALHE
from db import Database, ErroMaratona, Maratona, agora
from servicos.steam import PAUSA, ErroSteam, SteamClient
from servicos.steam_api import ErroSteamAPI, SteamAPI

log = logging.getLogger("maratonando.steam")

COR_STEAM = COR_DETALHE  # ciano (Manual da Marca)
BIBLIOTECA_VALIDA_HORAS = 6   # depois disso, lê a biblioteca de novo (alguém pode ter comprado jogo)
LOJA_VALIDA_DIAS = 3          # preço/promoção mudam: confere de novo a cada 3 dias
ANALISAR_POR_VEZ = 60         # jogos novos consultados na loja por /maratona baratos
POR_PAGINA = 10

COMO_DEIXAR_PUBLICO = ("Na Steam: **Perfil → Editar perfil → Configurações de privacidade → "
                       "Detalhes do jogo: Público**. Depois é só rodar o `/steam vincular` de novo.")


def antes_de(iso: Optional[str], **delta) -> bool:
    """True se a data (ISO) é mais velha que o intervalo, ou se não existe."""
    return iso is None or datetime.fromisoformat(iso) < datetime.now(timezone.utc) - timedelta(**delta)


# ---------------------------------------------------------------- resultado

class BaratosView(discord.ui.View):
    def __init__(self, opcoes: list[Opcao], com_steam: list[int], sem_steam: list[int], pendentes: int) -> None:
        super().__init__(timeout=900)
        self.opcoes = opcoes
        self.com_steam = com_steam
        self.sem_steam = sem_steam
        self.pendentes = pendentes
        self.pagina = 0
        self.montar()

    async def on_error(self, interaction, erro, item) -> None:
        await tratar_erro(interaction, erro)

    @property
    def paginas(self) -> int:
        return max(1, -(-len(self.opcoes) // POR_PAGINA))

    def montar(self) -> None:
        self.clear_items()
        if self.paginas > 1:
            for rotulo, passo in (("◀", -1), ("▶", 1)):
                b = discord.ui.Button(label=rotulo, style=discord.ButtonStyle.secondary)
                b.callback = self._mudar(passo)
                self.add_item(b)

    def _mudar(self, passo: int):
        async def callback(interaction: discord.Interaction) -> None:
            self.pagina = (self.pagina + passo) % self.paginas
            await interaction.response.edit_message(embed=self.embed(), view=self, allowed_mentions=SEM_PING)
        return callback

    @staticmethod
    def linha(n: int, o: Opcao) -> str:
        nome = f"`{n}.` **[{o.app.nome}]({o.app.url})**" + (" 📝" if o.da_lista else "")
        if o.todos_tem:
            return f"{nome} — ✅ todo mundo tem"
        if o.app.gratis:
            return f"{nome} — 🆓 grátis · {len(o.donos)} já {'tem' if len(o.donos) == 1 else 'têm'}"
        quem = " ".join(f"<@{u}>" for u in o.faltam)
        preco = o.app.preco_texto or reais(o.app.preco_centavos or 0)
        cada = f" ({preco} cada)" if len(o.faltam) > 1 else ""
        desconto = f" 💸 −{o.app.desconto}%" if o.app.desconto else ""
        return f"{nome} — 🛒 falta {len(o.faltam)}: {quem} · **{reais(o.custo)}**{cada}{desconto}"

    def embed(self) -> discord.Embed:
        e = discord.Embed(title="💰 Jogos mais baratos para o grupo", color=COR_STEAM, description=(
            "Jogos **multiplayer** que vocês já têm (ou quase), do que sai mais barato para o grupo "
            "ao mais caro. O custo é o preço atual na Steam × quantas pessoas ainda não têm o jogo. "
            "📝 = está na lista de alguém."))
        inicio = self.pagina * POR_PAGINA
        pagina = self.opcoes[inicio:inicio + POR_PAGINA]
        e.add_field(name="Jogos", value=juntar([self.linha(inicio + i + 1, o) for i, o in enumerate(pagina)],
                                                vazio="Nenhum jogo multiplayer em comum encontrado."),
                    inline=False)
        e.add_field(name=f"🎮 Na conta ({len(self.com_steam)})",
                    value=juntar([f"<@{u}>" for u in self.com_steam], vazio="—"))
        if self.sem_steam:
            e.add_field(name=f"❔ Fora da conta ({len(self.sem_steam)})",
                        value=juntar([f"<@{u}>" for u in self.sem_steam]) +
                        "\n-# Sem Steam vinculada ou com jogos privados. Use `/steam vincular`.")
        rodape = f"Página {self.pagina + 1} de {self.paginas}"
        if self.pendentes:
            rodape += (f" · Ainda estou analisando {plural(self.pendentes, 'jogo', 'jogos')} na loja; "
                       "rode de novo daqui a pouco para uma lista mais completa.")
        e.set_footer(text=rodape)
        return e


# ---------------------------------------------------------------- cog

class SteamCog(commands.Cog):
    steam = app_commands.Group(name="steam", description="Contas da Steam (para o bot saber quem tem cada jogo).",
                               guild_only=True)

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.db: Database = bot.db  # type: ignore[attr-defined]
        self.session: Optional[aiohttp.ClientSession] = None
        self.api: Optional[SteamAPI] = None
        self.loja: Optional[SteamClient] = None
        self.analisando = asyncio.Lock()
        self.tarefas: set[asyncio.Task] = set()
        self.cache_busca: dict[str, tuple[float, list[tuple[int, str]]]] = {}

    async def cog_load(self) -> None:
        self.session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=20))
        self.loja = SteamClient(self.session)
        chave = os.getenv("STEAM_API_KEY")
        if chave:
            self.api = SteamAPI(self.session, chave)
        else:
            log.warning("STEAM_API_KEY não configurada: /steam e /maratona baratos ficam desligados.")

    async def cog_unload(self) -> None:
        if self.session:
            await self.session.close()

    async def cog_app_command_error(self, interaction, erro) -> None:
        await tratar_erro(interaction, erro)

    def exigir_api(self) -> SteamAPI:
        if self.api is None:
            raise ErroMaratona("Falta a chave da API da Steam no `.env` (STEAM_API_KEY). O README explica como pegar.")
        return self.api

    @staticmethod
    def alvo(interaction: discord.Interaction, pessoa: Optional[discord.Member]) -> discord.abc.User:
        """De quem é a conta: a própria pessoa, ou outra (só para quem tem "Gerenciar servidor")."""
        if pessoa is None or pessoa.id == interaction.user.id:
            return interaction.user
        perms = getattr(interaction.user, "guild_permissions", None)
        if not (perms and perms.manage_guild):
            raise ErroMaratona("Só quem tem a permissão **Gerenciar servidor** pode mexer na Steam de outras pessoas.")
        if pessoa.bot:
            raise ErroMaratona("Bots não jogam. 🤖")
        return pessoa

    # ---- leitura das bibliotecas -----------------------------------

    async def ler_biblioteca(self, user_id: int, steamid: str) -> Optional[int]:
        """Lê os jogos da conta e guarda. Devolve quantos jogos, ou None se estiverem privados."""
        jogos = await self.exigir_api().jogos(steamid)
        if jogos is None:
            self.db.salvar_biblioteca(user_id, False, [])
            return None
        self.db.salvar_biblioteca(user_id, True, [int(j["appid"]) for j in jogos])
        self.db.catalogo_adicionar([(int(j["appid"]), j["name"]) for j in jogos if j.get("name")])
        return len(jogos)

    async def atualizar_bibliotecas(self, user_ids: list[int]) -> None:
        for uid, conta in self.db.contas_steam(user_ids).items():
            if antes_de(conta["atualizado_em"], hours=BIBLIOTECA_VALIDA_HORAS):
                await self.ler_biblioteca(uid, conta["steamid"])
                await asyncio.sleep(0.3)

    async def analisar_na_loja(self, appids: list[int], limite: int, progresso=None) -> int:
        """Consulta na loja os jogos que ainda não conheço (ou com preço velho). Devolve quantos ficaram faltando."""
        async with self.analisando:
            conhecidos = self.db.apps(appids)
            faltam = [a for a in appids
                      if a not in conhecidos or antes_de(conhecidos[a]["verificado_em"], days=LOJA_VALIDA_DIAS)]
            for n, appid in enumerate(faltam[:limite], start=1):
                detalhes = await self.loja.detalhes(appid)
                nome = conhecidos[appid]["nome"] if appid in conhecidos else ""
                self.db.salvar_app(app_de_detalhes(appid, detalhes, agora(), nome))
                if progresso and n % 10 == 0:
                    await progresso(n, min(len(faltam), limite))
                await asyncio.sleep(PAUSA)
            return max(0, len(faltam) - limite)

    # ---- "quem tem esse jogo?" (sorteio e roleta) --------------------

    async def buscar_loja(self, termo: str, tempo_max: float = 8.0) -> list[tuple[int, str]]:
        """Busca na loja (com memória de 10 minutos, para o autocompletar não repetir buscas)."""
        chave = termo.casefold().strip()
        guardado = self.cache_busca.get(chave)
        if guardado and time.monotonic() - guardado[0] < 600:
            return guardado[1]
        itens = await asyncio.wait_for(self.loja.buscar(termo), timeout=tempo_max)
        resultado = [(int(i["id"]), i["name"]) for i in itens if i.get("type", "app") == "app" and i.get("name")]
        self.cache_busca[chave] = (time.monotonic(), resultado)
        self.db.catalogo_adicionar(resultado)
        if len(self.cache_busca) > 500:  # não deixa a memória crescer para sempre
            self.cache_busca.pop(next(iter(self.cache_busca)))
        return resultado

    async def reconhecer(self, nome: str) -> Optional[tuple[int, str]]:
        """(jogo da Steam, nome oficial) quando o nome bate exatamente (ignorando maiúsculas e pontuação).

        Guarda o resultado (inclusive o "não achei"), para não perguntar à Steam toda hora.
        """
        conhecido = self.db.conn.execute(
            "SELECT appid, nome, verificado_em FROM steam_nomes WHERE chave = ?", (nome.casefold(),)).fetchone()
        if conhecido is not None and conhecido["appid"]:
            return conhecido["appid"], conhecido["nome"]
        if conhecido is not None and not antes_de(conhecido["verificado_em"], days=14):
            return None
        alvo = normalizar(nome)
        achado = next(((a, n) for a, n in await self.buscar_loja(nome) if normalizar(n) == alvo), None)
        if achado:
            self.lembrar_nome(nome, *achado)
        else:
            self.db.salvar_steam_nome(nome, None)
        return achado

    def lembrar_nome(self, digitado: str, appid: int, oficial: str) -> None:
        """Guarda que esse nome (e o nome oficial) é tal jogo da Steam."""
        self.db.salvar_steam_nome(oficial, appid)
        if digitado.casefold() != oficial.casefold():
            self.db.conn.execute("INSERT OR REPLACE INTO steam_nomes (chave, nome, appid, verificado_em)"
                                 " VALUES (?, ?, ?, ?)", (digitado.casefold(), oficial, appid, agora()))
            self.db.conn.commit()

    async def parecidos(self, nome: str) -> list[tuple[int, str]]:
        """Jogos da Steam com nome parecido (para o "você quis dizer…?").

        Junta duas fontes: a busca da Steam (pelo nome todo e pelas palavras maiores)
        e o catálogo de jogos que o bot já viu (bibliotecas do pessoal, buscas antigas).
        O catálogo é o que salva erros de digitação que a busca da Steam não acha.
        """
        itens: list[tuple[int, str]] = []
        for termo in termos_de_busca(nome):
            try:
                itens += await self.buscar_loja(termo)
            except Exception:
                pass
        return ranquear_parecidos(nome, itens + self.db.catalogo())

    async def sugestoes(self, texto: str) -> list[tuple[int, str]]:
        """Para o autocompletar: rápido (a resposta tem que sair em menos de 3 segundos)."""
        return (await self.buscar_loja(texto, tempo_max=2.0))[:10]

    async def appid_do_nome(self, nome: str) -> Optional[int]:
        """Descobre qual jogo da Steam é esse nome (e guarda, para não procurar de novo)."""
        achado = await self.reconhecer(nome)
        return achado[0] if achado else None

    async def app_atual(self, appid: int, nome: str = "") -> AppSteam:
        guardado = self.db.apps([appid]).get(appid)
        if guardado is not None and not antes_de(guardado["verificado_em"], days=LOJA_VALIDA_DIAS):
            return app_da_linha(guardado)
        app = app_de_detalhes(appid, await self.loja.detalhes(appid), agora(), nome)
        self.db.salvar_app(app)
        return app

    async def quem_tem(self, maratona_id: int, nome: str,
                       appid: Optional[int] = None) -> Optional[tuple[Opcao, list[int]]]:
        """Igual ao quem_tem_para, usando os jogadores da maratona."""
        return await self.quem_tem_para(self.db.jogadores(maratona_id), nome, appid)

    async def quem_tem_para(self, jogadores: list[int], nome: str,
                            appid: Optional[int] = None) -> Optional[tuple[Opcao, list[int]]]:
        """(quem tem / quem falta / custo, quem não dá para saber) de um jogo, para essas pessoas.

        None quando não dá para calcular: sem chave da Steam, ninguém com Steam vinculada,
        jogo não encontrado na Steam ou a Steam fora do ar. Nunca levanta erro: quem chama
        só mostra a informação se ela vier.
        """
        if self.api is None:
            return None
        try:
            await self.atualizar_bibliotecas(jogadores)
            bibliotecas = self.db.bibliotecas(jogadores)
            if not bibliotecas:
                return None
            appid = appid or await self.appid_do_nome(nome)
            if appid is None:
                return None
            app = await self.app_atual(appid, nome)
            donos = [u for u in bibliotecas if appid in bibliotecas[u]]
            faltam = [u for u in bibliotecas if appid not in bibliotecas[u]]
            sem_steam = [u for u in jogadores if u not in bibliotecas]
            return Opcao(app, donos, faltam), sem_steam
        except Exception as exc:  # informação extra: se falhar, o sorteio segue normal
            log.warning("Não consegui calcular quem tem %r: %s", nome, exc)
            return None

    async def mais_baratos_para(self, jogadores: list[int], limite: int = 3) -> list[Opcao]:
        """Os jogos multiplayer mais baratos para essas pessoas (lista vazia se não der para calcular)."""
        if self.api is None or not jogadores:
            return []
        try:
            await self.atualizar_bibliotecas(jogadores)
            bibliotecas = self.db.bibliotecas(jogadores)
            if not bibliotecas:
                return []
            candidatos = escolher_candidatos(bibliotecas, self.db.appids_das_listas_de(jogadores))
            await self.analisar_na_loja(candidatos, 20)  # rápido: o resto já está guardado de outras vezes
            apps = {a: app_da_linha(r) for a, r in self.db.apps(candidatos).items()}
            return calcular_opcoes(bibliotecas, apps, candidatos)[:limite]
        except Exception as exc:
            log.warning("Não consegui calcular os mais baratos: %s", exc)
            return []

    # ---- /maratona baratos -----------------------------------------

    async def mostrar_baratos(self, interaction: discord.Interaction, m: Maratona) -> None:
        self.exigir_api()
        jogadores = self.db.jogadores(m.id)
        if not jogadores:
            raise ErroMaratona("Ninguém está jogando ainda. Coloquem jogos na roleta ou cliquem em **🙋 Tô jogando**.")
        await interaction.response.defer(thinking=True)

        async def progresso(feitos: int, total: int) -> None:
            try:
                await interaction.edit_original_response(
                    content=f"🔎 Analisando jogos na loja da Steam… {feitos}/{total} (só demora assim na primeira vez)")
            except discord.HTTPException:
                pass

        try:
            await self.atualizar_bibliotecas(jogadores)
            bibliotecas = self.db.bibliotecas(jogadores)
            if not bibliotecas:
                raise ErroMaratona("Ninguém que está jogando tem a Steam vinculada (com jogos públicos). "
                                   "Use `/steam vincular` primeiro.")
            da_lista = self.db.appids_das_listas_de(jogadores)
            candidatos = escolher_candidatos(bibliotecas, da_lista)
            pendentes = await self.analisar_na_loja(candidatos, ANALISAR_POR_VEZ, progresso)
        except (ErroSteamAPI, ErroSteam, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ErroMaratona(f"Não consegui falar com a Steam agora ({exc}). Tenta de novo daqui a pouco.")

        apps = {a: app_da_linha(r) for a, r in self.db.apps(candidatos).items()}
        opcoes = calcular_opcoes(bibliotecas, apps, candidatos, da_lista)
        com_steam = [u for u in jogadores if u in bibliotecas]
        sem_steam = [u for u in jogadores if u not in bibliotecas]
        view = BaratosView(opcoes, com_steam, sem_steam, pendentes)
        await interaction.edit_original_response(content=None, embed=view.embed(), view=view,
                                                 allowed_mentions=SEM_PING)
        if pendentes:  # continua analisando em segundo plano, para a próxima vez ficar completa
            tarefa = asyncio.create_task(self._analisar_resto(candidatos))
            self.tarefas.add(tarefa)
            tarefa.add_done_callback(self.tarefas.discard)

    async def _analisar_resto(self, candidatos: list[int]) -> None:
        try:
            await self.analisar_na_loja(candidatos, len(candidatos))
        except Exception:
            log.exception("Erro analisando jogos em segundo plano")

    # ---- /steam ----------------------------------------------------

    @steam.command(name="vincular", description="Liga uma conta da Steam ao Discord (a sua ou, se for admin, a de outra pessoa).")
    @app_commands.describe(perfil="Link do perfil na Steam (ex.: https://steamcommunity.com/id/fulano)",
                           pessoa="De quem é a conta (deixe vazio para a sua)")
    async def cmd_vincular(self, interaction: discord.Interaction, perfil: str,
                           pessoa: Optional[discord.Member] = None) -> None:
        api = self.exigir_api()
        alvo = self.alvo(interaction, pessoa)
        lido = ler_perfil(perfil)
        if lido is None:
            raise ErroMaratona("Não entendi esse perfil. Cole o link completo, tipo "
                               "`https://steamcommunity.com/id/fulano` ou `https://steamcommunity.com/profiles/7656119…`.")
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            tipo, valor = lido
            steamid = valor if tipo == "id" else await api.resolver_nome(valor)
            dados = await api.perfil(steamid) if steamid else None
            if dados is None:
                raise ErroMaratona(f"Não achei esse perfil na Steam (`{perfil}`). Confere o link?")
            self.db.vincular_steam(alvo.id, steamid, dados.get("personaname"), interaction.user.id)
            total = await self.ler_biblioteca(alvo.id, steamid)
        except (ErroSteamAPI, aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise ErroMaratona(f"Não consegui falar com a Steam agora ({exc}).")

        de_quem = "Sua Steam" if alvo.id == interaction.user.id else f"A Steam de {alvo.mention}"
        texto = f"✅ {de_quem} foi vinculada: **{dados.get('personaname', steamid)}**"
        if total is None:
            texto += f"\n\n🔒 Mas os jogos dessa conta estão **privados**, então não consigo ver. {COMO_DEIXAR_PUBLICO}"
        else:
            texto += f" ({plural(total, 'jogo', 'jogos')})."
        await interaction.followup.send(texto, ephemeral=True, allowed_mentions=SEM_PING)

    @steam.command(name="desvincular", description="Desliga a conta da Steam (a sua ou, se for admin, a de outra pessoa).")
    @app_commands.describe(pessoa="De quem (deixe vazio para a sua)")
    async def cmd_desvincular(self, interaction: discord.Interaction,
                              pessoa: Optional[discord.Member] = None) -> None:
        alvo = self.alvo(interaction, pessoa)
        if not self.db.desvincular_steam(alvo.id):
            raise ErroMaratona("Não tinha Steam vinculada.")
        await interaction.response.send_message("🗑️ Steam desvinculada e a lista de jogos apagada.", ephemeral=True)

    @steam.command(name="ver", description="Mostra quem da maratona tem a Steam vinculada.")
    async def cmd_ver(self, interaction: discord.Interaction) -> None:
        m = self.db.maratona_aberta(interaction.guild_id)
        pessoas = self.db.jogadores(m.id) if m else [interaction.user.id]
        contas = self.db.contas_steam(pessoas)
        linhas = []
        for uid in pessoas:
            c = contas.get(uid)
            if c is None:
                linhas.append(f"❌ <@{uid}> — não vinculada")
            elif not c["publico"]:
                linhas.append(f"🔒 <@{uid}> — **{c['nome']}** (jogos privados)")
            else:
                linhas.append(f"✅ <@{uid}> — **{c['nome']}** ({plural(c['total_jogos'], 'jogo', 'jogos')})")
        e = discord.Embed(title="🎮 Contas da Steam" + (" da maratona" if m else ""), color=COR_STEAM,
                          description=juntar(linhas, limite=4000))
        if any(c is None or not c["publico"] for c in (contas.get(u) for u in pessoas)):
            e.set_footer(text="Quem tem 'Gerenciar servidor' pode vincular pelos outros: "
                              "/steam vincular perfil:<link> pessoa:@alguém")
        await interaction.response.send_message(embed=e, ephemeral=True, allowed_mentions=SEM_PING)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(SteamCog(bot))
