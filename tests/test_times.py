"""Testes da divisão de times."""

import random

import pytest

from times import dividir_em_times


def test_times_equilibrados_e_ninguem_some():
    jogadores = list(range(1, 8))  # 7 pessoas
    times = dividir_em_times(jogadores, 2, random.Random(1))
    assert sorted(len(t) for t in times) == [3, 4]
    assert sorted(j for t in times for j in t) == jogadores


def test_tres_times_e_repetidos_ignorados():
    times = dividir_em_times([1, 2, 3, 4, 5, 6, 6], 3, random.Random(2))
    assert [len(t) for t in times] == [2, 2, 2]


def test_sorteio_muda_com_o_acaso():
    jogadores = list(range(10))
    resultados = {tuple(map(tuple, dividir_em_times(jogadores, 2, random.Random(s)))) for s in range(20)}
    assert len(resultados) > 1


def test_menos_de_dois_times_nao_pode():
    with pytest.raises(ValueError):
        dividir_em_times([1, 2], 1)
