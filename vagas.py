"""Vagas do /chamar (e do /bora): quem está dentro, quem está na fila e quem acabou de ganhar a vaga.

Regra: com N vagas, os N primeiros que clicaram "Bora" estão dentro; o resto fica na fila, na ordem
em que clicou. Se alguém de dentro sai, o primeiro da fila sobe sozinho.

Módulo puro, testado em tests/test_chamar.py.
"""

from __future__ import annotations


def dividir(bora: list[int], vagas: int | None) -> tuple[list[int], list[int]]:
    """(dentro, fila). bora = quem clicou "Bora", na ordem dos cliques. Sem número de vagas, todos estão dentro."""
    if not vagas:
        return list(bora), []
    return list(bora[:vagas]), list(bora[vagas:])


def ganharam_vaga(antes: list[int], depois: list[int], quem_clicou: int) -> list[int]:
    """Quem passou a estar dentro por causa do clique de outra pessoa (subiu da fila)."""
    return [u for u in depois if u not in antes and u != quem_clicou]


def posicao_na_fila(fila: list[int], user_id: int) -> int | None:
    """1 = o próximo a entrar. None = não está na fila."""
    return fila.index(user_id) + 1 if user_id in fila else None
