"""Nomes de jogos digitados "de qualquer jeito": parecidos, repetidos e buscas.

Módulo puro (sem internet nem banco), testado em tests/test_nomes.py.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from radar import normalizar

MINIMO_PARECIDO = 0.55   # abaixo disso, o nome sugerido é diferente demais para perguntar
MINIMO_REPETIDO = 0.92   # acima disso (e com os mesmos números), é o mesmo jogo escrito diferente


def _numeros(nome: str) -> list[str]:
    return re.findall(r"\d+", normalizar(nome))


def semelhanca(digitado: str, candidato: str) -> float:
    """De 0 a 1: o quanto o nome digitado parece o nome do jogo.

    Nome incompleto também conta ("lethal" → "Lethal Company"), desde que tenha
    pelo menos 4 letras.
    """
    a, b = normalizar(digitado), normalizar(candidato)
    if not a or not b:
        return 0.0
    nota = SequenceMatcher(None, a, b).ratio()
    if len(a) >= 4 and b.startswith(a):
        nota = max(nota, 0.8)
    if len(a) >= 5 and len(b) > len(a):
        # Começo do nome com erro de digitação ("dale dawnson" → "Dale & Dawson Stationery Supplies"):
        # compara com o pedaço do mesmo tamanho do começo do nome do jogo.
        nota = max(nota, 0.9 * SequenceMatcher(None, a, b[:len(a)]).ratio())
    return nota


def mesmo_jogo(a: str, b: str) -> bool:
    """Os dois nomes são o mesmo jogo escrito de jeitos diferentes?

    "R.E.P.O." = "repo" e "Lethal Company" = "lethal  company!" são o mesmo.
    "Portal" ≠ "Portal 2" e "Left 4 Dead" ≠ "Left 4 Dead 2": números diferentes
    quase sempre são jogos diferentes (continuações).
    """
    na, nb = normalizar(a), normalizar(b)
    if na == nb:
        return True
    if _numeros(a) != _numeros(b):
        return False
    return SequenceMatcher(None, na, nb).ratio() >= MINIMO_REPETIDO


def termos_de_busca(digitado: str) -> list[str]:
    """O que pesquisar na Steam para achar o jogo mesmo com erro de digitação.

    A busca da Steam procura por palavras inteiras: "letal compani" não acha nada,
    mas "compani"… também não. Então o bot pesquisa o nome todo e também as
    palavras maiores separadas, e depois compara os resultados com o que foi digitado.
    """
    palavras = sorted({p for p in normalizar(digitado).split() if len(p) >= 4}, key=len, reverse=True)
    termos = [digitado.strip()] + palavras[:2]
    return list(dict.fromkeys(t for t in termos if t))


def ranquear_parecidos(digitado: str, itens: list[tuple[int, str]],
                       limite: int = 3) -> list[tuple[int, str]]:
    """Dos jogos encontrados, os mais parecidos com o que foi digitado (sem repetir)."""
    notas: dict[int, tuple[float, str]] = {}
    for appid, nome in itens:
        nota = semelhanca(digitado, nome)
        if nota >= MINIMO_PARECIDO and (appid not in notas or nota > notas[appid][0]):
            notas[appid] = (nota, nome)
    ordem = sorted(notas.items(), key=lambda x: (-x[1][0], x[1][1]))
    return [(appid, nome) for appid, (_, nome) in ordem[:limite]]


# ---------------------------------------------------------------- escolha do autocompletar

_ESCOLHA_STEAM = re.compile(r"^(\d{1,10})\|(.+)$", re.S)


def valor_escolha(appid: int, nome: str) -> str:
    """O que vai no "value" de uma opção do autocompletar (limite do Discord: 100 caracteres)."""
    return f"{appid}|{nome}"[:100]


def ler_escolha(valor: str) -> tuple[str, int | None]:
    """Desfaz o valor_escolha. Se a pessoa digitou sem escolher da lista, volta (texto, None)."""
    m = _ESCOLHA_STEAM.match(valor.strip())
    if m:
        return m.group(2).strip(), int(m.group(1))
    return valor.strip(), None
