"""Testes da leitura do número de jogadores (descrições reais da Steam) e do que o grupo ensina."""

import pytest

from db import Database, ErroMaratona
from jogadores import cabe, extrair, extrair_de_detalhes, faixa_texto


@pytest.mark.parametrize("texto, single, esperado", [
    # trechos reais da loja (out/2026)
    ("An online co-op horror game with up to 6 players. You and up to 5 friends can venture into "
     "terrifying environments", True, (1, 6)),                                                     # R.E.P.O.
    ("Multiplayer in PEAK is friends-only - invite up to three other scouts through Steam to climb "
     "together. Climb with a total of 4 friends", True, (1, 4)),                                   # PEAK
    ("A co-op horror about scavenging at abandoned moons to sell scrap to the Company.", True, None),  # Lethal
    # jeitos comuns de escrever
    ("Supports 1-4 players in online co-op", False, (1, 4)),
    ("2 to 8 players party game", False, (2, 8)),
    ("Chaotic 4-player co-op cooking", True, (1, 4)),
    ("8 players online party", False, (2, 8)),
    ("<p>Play with up&nbsp;to <b>eight</b> friends</p>", False, (2, 8)),
    ("Um jogo cooperativo para até 4 jogadores online!", False, (2, 4)),
    ("Você e até 3 amigos", False, (2, 4)),
    ("De 2 a 6 jogadores", False, (2, 6)),
    # o que NÃO é número de jogadores
    ("Includes 5 player characters and 12 levels", True, None),
    ("Join over 1000 players online in a massive world", False, None),
    ("", False, None),
])
def test_extrair(texto, single, esperado):
    assert extrair(texto, single) == esperado


def test_extrair_de_detalhes_da_steam():
    detalhes = {"short_description": "An online co-op horror game with up to 6 players.",
                "categories": [{"id": 2, "description": "Single-player"}, {"id": 1}]}
    assert extrair_de_detalhes(detalhes) == (1, 6)
    detalhes["categories"] = [{"id": 1}]
    assert extrair_de_detalhes(detalhes) == (2, 6)
    assert extrair_de_detalhes(None) is None


def test_cabe_e_texto():
    assert cabe((1, 4), 4) is True and cabe((1, 4), 5) is False and cabe((2, 8), 1) is False
    assert cabe(None, 4) is None
    assert faixa_texto((1, 4)) == "1 a 4 jogadores"
    assert faixa_texto((4, 4)) == "4 jogadores"
    assert faixa_texto(None) == "nº de jogadores desconhecido"


def test_o_que_o_grupo_ensina_vale_mais_que_a_steam():
    db = Database(":memory:")
    db.salvar_jogadores_steam(10, None)                  # a descrição não dizia
    assert db.jogadores_de([10])[10]["maximo"] is None
    db.definir_jogadores_grupo(10, 1, 4, user_id=99)    # o grupo ensinou
    db.salvar_jogadores_steam(10, (1, 32))              # a Steam (ou um erro de leitura) não sobrescreve
    row = db.jogadores_de([10])[10]
    assert (row["minimo"], row["maximo"], row["fonte"], row["definido_por"]) == (1, 4, "grupo", 99)
    db.salvar_jogadores_steam(20, (1, 6))
    db.salvar_jogadores_steam(20, (2, 8))               # leitura nova da Steam atualiza a da Steam
    assert (db.jogadores_de([20])[20]["minimo"], db.jogadores_de([20])[20]["maximo"]) == (2, 8)
    with pytest.raises(ErroMaratona):
        db.definir_jogadores_grupo(10, 5, 4, user_id=99)
    assert set(db.jogadores_de([10, 20, 30])) == {10, 20}
    db.fechar()
