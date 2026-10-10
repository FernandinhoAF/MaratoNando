"""Divisão de jogadores em times (puro: sem Discord, fácil de testar)."""

from __future__ import annotations

import random

CORES = [("🔴", "Vermelho"), ("🔵", "Azul"), ("🟢", "Verde"), ("🟡", "Amarelo"),
         ("🟣", "Roxo"), ("🟠", "Laranja"), ("⚪", "Branco"), ("⚫", "Preto")]
MAX_TIMES = len(CORES)

_rng = random.SystemRandom()


def dividir_em_times(jogadores: list[int], quantidade: int, rng: random.Random | None = None) -> list[list[int]]:
    """Embaralha e distribui como quem dá cartas: um para cada time, em rodízio.

    Assim os times ficam com o mesmo tamanho, ou no máximo 1 pessoa de diferença.
    """
    if quantidade < 2:
        raise ValueError("Precisa de pelo menos 2 times.")
    embaralhados = list(dict.fromkeys(jogadores))  # tira repetidos mantendo a ordem
    (rng or _rng).shuffle(embaralhados)
    times: list[list[int]] = [[] for _ in range(quantidade)]
    for i, jogador in enumerate(embaralhados):
        times[i % quantidade].append(jogador)
    return times
