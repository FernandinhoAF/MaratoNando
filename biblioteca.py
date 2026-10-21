"""Regras do "quanto o grupo gasta": cruza as bibliotecas da Steam dos jogadores.

Para cada jogo multiplayer:
  • donos   = jogadores que já têm o jogo
  • faltam  = jogadores com Steam vinculada que não têm
  • custo   = preço atual × quantas pessoas faltam (grátis = R$ 0)

Quem não vinculou a Steam (ou deixa os jogos privados) não entra na conta,
mas aparece avisado na mensagem.

Módulo puro: sem internet e sem banco, só dados entrando e saindo.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from radar import CATEGORIAS_COOP, CATEGORIAS_MULTIPLAYER

STEAMID_BASE = 76561197960265728  # todo SteamID64 de usuário começa a partir daqui
MAX_CANDIDATOS = 150              # jogos analisados por vez no /maratona baratos


# ---------------------------------------------------------------- perfil

_PERFIL_ID = re.compile(r"steamcommunity\.com/profiles/(\d{17})")
_PERFIL_NOME = re.compile(r"steamcommunity\.com/id/([A-Za-z0-9_-]{2,32})")


def ler_perfil(texto: str) -> tuple[str, str] | None:
    """Entende o que a pessoa colou: link do perfil, ID numérico ou nome personalizado.

    Devolve ("id", "7656119...") quando já é o ID numérico,
    ("nome", "fulano") quando é o nome personalizado (precisa perguntar à Steam o ID),
    ou None quando não dá para entender.
    """
    texto = texto.strip()
    if m := _PERFIL_ID.search(texto):
        return "id", m.group(1)
    if m := _PERFIL_NOME.search(texto):
        return "nome", m.group(1)
    if re.fullmatch(r"\d{17}", texto) and int(texto) >= STEAMID_BASE:
        return "id", texto
    if re.fullmatch(r"[A-Za-z0-9_-]{2,32}", texto):
        return "nome", texto
    return None


# ---------------------------------------------------------------- jogos da loja

@dataclass(frozen=True)
class AppSteam:
    appid: int
    nome: str
    gratis: bool
    preco_centavos: int | None  # None = não está à venda no Brasil
    preco_texto: str | None
    desconto: int
    multiplayer: bool
    coop: bool
    verificado_em: str

    @property
    def url(self) -> str:
        return f"https://store.steampowered.com/app/{self.appid}/"


def app_de_detalhes(appid: int, detalhes: dict | None, agora_iso: str, nome_reserva: str = "") -> AppSteam:
    """Monta um AppSteam a partir do campo "data" de /api/appdetails (None = jogo sumiu da loja)."""
    if not detalhes:
        return AppSteam(appid, nome_reserva or str(appid), False, None, None, 0, False, False, agora_iso)
    categorias = {c.get("id") for c in detalhes.get("categories", [])}
    preco = detalhes.get("price_overview") or {}
    return AppSteam(
        appid=appid,
        nome=detalhes.get("name") or nome_reserva or str(appid),
        gratis=bool(detalhes.get("is_free")),
        preco_centavos=preco.get("final"),
        preco_texto=preco.get("final_formatted"),
        desconto=int(preco.get("discount_percent") or 0),
        multiplayer=bool(categorias & CATEGORIAS_MULTIPLAYER),
        coop=bool(categorias & CATEGORIAS_COOP),
        verificado_em=agora_iso,
    )


def app_da_linha(row) -> AppSteam:
    """Converte uma linha da tabela steam_apps em AppSteam."""
    return AppSteam(row["appid"], row["nome"], bool(row["gratis"]), row["preco_centavos"], row["preco_texto"],
                    row["desconto"], bool(row["multiplayer"]), bool(row["coop"]), row["verificado_em"])


# ---------------------------------------------------------------- cálculo

@dataclass(frozen=True)
class Opcao:
    app: AppSteam
    donos: list[int]
    faltam: list[int]
    da_lista: bool = False  # alguém colocou esse jogo na lista pessoal

    @property
    def custo(self) -> int:
        """Quanto o grupo gasta, em centavos, para todo mundo (com Steam) ter o jogo."""
        if self.app.gratis or not self.faltam:
            return 0
        return (self.app.preco_centavos or 0) * len(self.faltam)

    @property
    def todos_tem(self) -> bool:
        return not self.faltam


def escolher_candidatos(bibliotecas: dict[int, set[int]], extras: set[int] = frozenset(),
                        limite: int = MAX_CANDIDATOS) -> list[int]:
    """Quais jogos vale a pena analisar.

    Com várias bibliotecas, o bot junta milhares de jogos. Para não consultar a
    Steam sobre todos, fica com os que mais jogadores têm (um jogo que só 1 de 6
    pessoas tem raramente é o mais barato para o grupo) e com os jogos que estão
    nas listas pessoais (são os que vocês querem jogar).
    """
    contagem: dict[int, int] = {}
    for jogos in bibliotecas.values():
        for appid in jogos:
            contagem[appid] = contagem.get(appid, 0) + 1
    minimo = 1 if len(bibliotecas) <= 2 else 2
    populares = sorted((a for a, n in contagem.items() if n >= minimo), key=lambda a: (-contagem[a], a))
    saida = list(dict.fromkeys(list(extras) + populares))
    return saida[:limite]


def calcular_opcoes(bibliotecas: dict[int, set[int]], apps: dict[int, AppSteam],
                    candidatos: list[int], da_lista: set[int] = frozenset()) -> list[Opcao]:
    """Só jogos multiplayer, do mais barato para o grupo ao mais caro.

    Empate no custo: ganha o que mais gente já tem.
    Jogos que não estão à venda no Brasil e que alguém não tem ficam de fora
    (não dá para saber quanto custaria).
    """
    jogadores = list(bibliotecas)
    opcoes = []
    for appid in candidatos:
        app = apps.get(appid)
        if app is None or not app.multiplayer:
            continue
        donos = [u for u in jogadores if appid in bibliotecas[u]]
        faltam = [u for u in jogadores if appid not in bibliotecas[u]]
        if faltam and not app.gratis and app.preco_centavos is None:
            continue
        opcoes.append(Opcao(app, donos, faltam, appid in da_lista))
    opcoes.sort(key=lambda o: (o.custo, -len(o.donos), o.app.nome.casefold()))
    return opcoes


def reais(centavos: int) -> str:
    return f"R$ {centavos / 100:.2f}".replace(".", ",")


def resumo_grupo(o: Opcao, sem_steam: list[int] = (), mencoes: bool = True) -> str:
    """Texto de "quem tem / quanto custa" de um jogo, para o sorteio e para quem coloca jogo na roleta."""
    def nomes(ids):
        return " ".join(f"<@{u}>" for u in ids) if mencoes else plural_pessoas(len(ids))

    def quantos_tem() -> str:
        if not o.donos:
            return "❌ Ninguém tem ainda"
        return f"✅ {len(o.donos)} {'tem' if len(o.donos) == 1 else 'têm'}"

    if o.todos_tem:
        texto = "✅ Todo mundo já tem"
    elif o.app.gratis:
        texto = f"🆓 Grátis na Steam · {quantos_tem()}"
    else:
        preco = o.app.preco_texto or reais(o.app.preco_centavos or 0)
        if o.app.preco_centavos is None:
            texto = f"{quantos_tem()} · 🛒 falta para {nomes(o.faltam)} (sem preço no Brasil)"
        else:
            cada = f"{preco} cada · " if len(o.faltam) > 1 else ""
            desconto = f" · 💸 −{o.app.desconto}%" if o.app.desconto else ""
            texto = (f"{quantos_tem()} · 🛒 falta para {nomes(o.faltam)} "
                     f"({cada}**{reais(o.custo)}** no total){desconto}")
    if sem_steam:
        texto += f"\n❔ Não sei de {nomes(sem_steam)} (sem Steam vinculada ou com jogos privados)"
    return texto


def plural_pessoas(n: int) -> str:
    return f"{n} pessoa" if n == 1 else f"{n} pessoas"


def rodape_card(o: Opcao) -> str:
    """A linha curta de "quem tem" que vai no rodapé do card da roleta (sem menções, em maiúsculas)."""
    if o.todos_tem:
        return "TODO MUNDO JÁ TEM"
    if o.app.gratis:
        return "GRÁTIS NA STEAM"
    tem = f"{len(o.donos)} {'TEM' if len(o.donos) == 1 else 'TÊM'}" if o.donos else "NINGUÉM TEM"
    if o.app.preco_centavos is None:
        return f"{tem} · SEM PREÇO NO BRASIL"
    return f"{tem} · FALTA PRA {plural_pessoas(len(o.faltam)).upper()}: {reais(o.custo)}"
