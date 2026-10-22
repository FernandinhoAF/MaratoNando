"""Quantas pessoas cada jogo aceita, lido da descrição da loja da Steam.

A Steam não tem um campo organizado com "número de jogadores": só as categorias
(single-player, multiplayer, co-op). Mas a descrição costuma dizer, em texto livre:
"up to 6 players", "you and up to 5 friends", "1-4 players", "até 4 jogadores"...
Este módulo procura essas frases. Quando não acha, devolve None, e o grupo pode
ensinar o bot pelo botão 👥 da ficha do /jogo (o que o grupo ensina sempre vale mais).

Módulo puro (sem Discord nem internet), testado em tests/test_jogadores.py.
"""

from __future__ import annotations

import html
import re
import unicodedata

MAX_RAZOAVEL = 100  # acima disso é texto de MMO/battle royale ("1000 players online"), não um limite de grupo

_NUMEROS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "twelve": 12, "sixteen": 16, "twenty": 20,
    "um": 1, "uma": 1, "dois": 2, "duas": 2, "tres": 3, "quatro": 4, "cinco": 5, "seis": 6,
    "sete": 7, "oito": 8, "nove": 9, "dez": 10, "doze": 12, "dezesseis": 16, "vinte": 20,
}
_N = r"(\d{1,3}|" + "|".join(_NUMEROS) + r")"
_GENTE = r"(?:players?|people|friends?|jogadores|pessoas|amigos|amigas)"

# (expressão, como transformar em (mínimo, máximo))
_REGRAS: list[tuple[re.Pattern, str]] = [
    # "1-4 players", "2 to 8 players", "de 2 a 8 jogadores"
    (re.compile(rf"\b{_N}\s*(?:-|to|a|ate)\s*{_N}\s*(?:-\s*)?(?:online\s+)?{_GENTE}"), "faixa"),
    # "you and up to 5 friends", "voce e ate 3 amigos" (a pessoa + os amigos)
    (re.compile(rf"\b(?:you|voce) and up to {_N}\b"), "mais_um"),
    (re.compile(rf"\b(?:voce) e ate {_N}\b"), "mais_um"),
    # "invite up to three other scouts", "ate 3 outros jogadores"
    (re.compile(rf"\bup to {_N} (?:other|more)\b"), "mais_um"),
    (re.compile(rf"\bate {_N} (?:outros|outras)\b"), "mais_um"),
    # "up to 6 players", "for up to 4 online players", "ate 4 jogadores"
    (re.compile(rf"\b(?:up to|ate|max(?:imum)? of|maximo de) {_N} (?:\w+[ -]){{0,2}}{_GENTE}"), "maximo"),
    # "4-player co-op" (com hífen é quase sempre o número de jogadores)
    (re.compile(rf"\b{_N}-players?\b"), "maximo"),
    # "8 players online", "4 player co-op" (sem hífen, só com uma palavra de multiplayer logo depois,
    # para não confundir com "5 player characters")
    (re.compile(rf"\b{_N} players? (?:co-?op|coop|online|multiplayer|local|party|split|versus|pvp|game)"), "maximo"),
    # "a total of 4 friends", "um total de 4"
    (re.compile(rf"\b(?:a total of|um total de) {_N}\b"), "maximo"),
]


def _numero(texto: str) -> int:
    return int(texto) if texto.isdigit() else _NUMEROS[texto]


def _limpar(texto: str) -> str:
    """Tira HTML e acentos, deixa minúsculo e com espaços simples."""
    texto = html.unescape(re.sub(r"<[^>]+>", " ", texto or ""))
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(texto.lower().replace("–", "-").replace("—", "-").split())


def extrair(texto: str, tem_single_player: bool = False) -> tuple[int, int] | None:
    """(mínimo, máximo) de jogadores que o texto diz, ou None se não diz.

    Se o texto der vários números, vale o maior máximo (descrições repetem a informação
    de jeitos diferentes, e "até 4 no online" costuma ser o limite de verdade).
    O mínimo é 1 se o jogo tem modo single-player; senão, 2 (ou o que a faixa disser).
    """
    t = _limpar(texto)
    minimos: list[int] = []
    maximos: list[int] = []
    for regra, tipo in _REGRAS:
        for m in regra.finditer(t):
            numeros = [_numero(g) for g in m.groups()]
            if tipo == "faixa":
                a, b = sorted(numeros)
                if a == 0 or b > MAX_RAZOAVEL:
                    continue
                minimos.append(a)
                maximos.append(b)
            elif tipo == "mais_um":
                maximos.append(numeros[0] + 1)
            else:
                maximos.append(numeros[0])
    maximos = [m for m in maximos if 2 <= m <= MAX_RAZOAVEL]
    if not maximos:
        return None
    maximo = max(maximos)
    minimo = 1 if tem_single_player else min([m for m in minimos if m <= maximo] or [2])
    return min(minimo, maximo), maximo


CATEGORIA_SINGLE_PLAYER = 2


def extrair_de_detalhes(detalhes: dict | None) -> tuple[int, int] | None:
    """Mesma coisa, a partir da resposta do /api/appdetails da Steam."""
    if not detalhes:
        return None
    texto = " ".join(detalhes.get(c) or "" for c in ("short_description", "about_the_game", "detailed_description"))
    single = any(c.get("id") == CATEGORIA_SINGLE_PLAYER for c in detalhes.get("categories", []))
    return extrair(texto, single)


def cabe(faixa: tuple[int, int] | None, pessoas: int) -> bool | None:
    """O jogo serve para esse número de pessoas? None = não sei."""
    if faixa is None:
        return None
    return faixa[0] <= pessoas <= faixa[1]


def faixa_texto(faixa: tuple[int, int] | None) -> str:
    """(1, 4) → "1 a 4 jogadores"; (4, 4) → "4 jogadores"; None → "nº de jogadores desconhecido"."""
    if faixa is None:
        return "nº de jogadores desconhecido"
    a, b = faixa
    return f"{b} jogadores" if a == b else f"{a} a {b} jogadores"
