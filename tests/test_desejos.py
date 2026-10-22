"""Testes da lista de desejos do grupo (listas do bot + lista de desejos da Steam)."""

import pytest

from db import Database
from desejos import agrupar

ANA, BIA, CAIO, DUDA = 1, 2, 3, 4
SERVIDOR, OUTRO = 100, 200


def test_agrupar_ordena_por_quantas_pessoas_querem():
    linhas = [(ANA, 10, "lista"), (BIA, 10, "steam"), (CAIO, 10, "steam"),
              (ANA, 20, "steam"), (BIA, 20, "steam"),
              (CAIO, 30, "lista"),
              (DUDA, 40, "lista")]
    bibliotecas = {ANA: set(), BIA: set(), CAIO: set(), DUDA: {40}}  # Duda já comprou o 40
    d = agrupar(linhas, bibliotecas)
    assert [x.appid for x in d] == [10, 20, 30]           # o 40 sai: quem queria já tem
    assert d[0].querem == (ANA, BIA, CAIO) and d[0].do_grupo and d[0].da_steam
    assert not d[2].do_grupo and not d[2].da_steam


def test_quem_ja_tem_sai_de_quem_quer():
    linhas = [(ANA, 10, "lista"), (BIA, 10, "steam")]
    d = agrupar(linhas, {ANA: {10}, BIA: set(), CAIO: {10}})
    assert d[0].querem == (BIA,) and d[0].ja_tem == (ANA, CAIO) and not d[0].do_grupo


def test_empate_maior_desconto_primeiro():
    linhas = [(ANA, 10, "lista"), (ANA, 20, "lista")]
    assert [x.appid for x in agrupar(linhas, {}, {10: 0, 20: 50})] == [20, 10]


@pytest.fixture
def db():
    banco = Database(":memory:")
    for u in (ANA, BIA, CAIO):
        banco.registrar_usuario_servidor(u, SERVIDOR)
    banco.registrar_usuario_servidor(DUDA, OUTRO)
    yield banco
    banco.fechar()


def test_desejos_do_servidor_juntam_as_duas_fontes(db):
    db.salvar_steam_nome("PEAK", 10)
    db.salvar_na_lista(ANA, ["PEAK"])
    db.salvar_desejos(BIA, [10, 20])
    db.salvar_desejos(DUDA, [10])                 # outro servidor: não entra
    assert sorted(db.desejos_do_servidor(SERVIDOR)) == [(ANA, 10, "lista"), (BIA, 10, "steam"), (BIA, 20, "steam")]
    db.salvar_desejos(BIA, [20])                  # leu de novo: o 10 saiu da lista de desejos da Steam
    assert sorted(db.desejos_do_servidor(SERVIDOR)) == [(ANA, 10, "lista"), (BIA, 20, "steam")]


def test_aviso_de_promocao_so_vira_do_grupo_com_duas_pessoas(db):
    db.salvar_steam_nome("PEAK", 10)
    db.salvar_na_lista(ANA, ["PEAK"])
    db.salvar_desejos(CAIO, [30])                 # só o Caio quer o 30, e só na Steam
    assert db.interessados(SERVIDOR, 10) == [ANA]  # lista pessoal: avisa como sempre
    assert db.interessados(SERVIDOR, 30) == []     # um só, na Steam: a Steam já avisa por e-mail
    assert 30 not in db.appids_das_listas()

    db.salvar_desejos(BIA, [30])                  # agora são dois: desejo do grupo
    assert db.interessados(SERVIDOR, 30) == [BIA, CAIO]
    assert db.appids_das_listas()[30] == ""        # vigiado, nome ainda desconhecido
    db.catalogo_adicionar([(30, "Jogo Trinta")])
    assert db.appids_das_listas()[30] == "Jogo Trinta"

    db.salvar_desejos(BIA, [10])                  # Ana (lista) + Bia (Steam) querem o 10
    assert db.interessados(SERVIDOR, 10) == [ANA, BIA]


def test_desejo_do_grupo_conta_por_servidor(db):
    db.registrar_usuario_servidor(DUDA, OUTRO)
    db.salvar_desejos(CAIO, [50])                 # Caio no SERVIDOR, Duda no OUTRO: nenhum tem dois
    db.salvar_desejos(DUDA, [50])
    assert db.desejos_do_grupo_em_algum_servidor() == set()
