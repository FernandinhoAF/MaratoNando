"""Testes da ficha do jogo (/jogo ver)."""

import ficha
from db import Database

DETALHES = {
    "name": "PEAK",
    "is_free": False,
    "short_description": "PEAK é um jogo de escalada <b>cooperativo</b> em que o menor erro pode ser fatal.",
    "price_overview": {"initial": 1999, "final": 1487, "discount_percent": 26,
                       "initial_formatted": "R$ 19,99", "final_formatted": "R$ 14,87"},
    "categories": [{"id": 2}, {"id": 1}, {"id": 9}, {"id": 38}, {"id": 62}],
    "genres": [{"description": "Ação"}, {"description": "Aventura"}, {"description": "Indie"}],
    "release_date": {"coming_soon": False, "date": "16 jun. 2025"},
}


def test_preco():
    assert ficha.preco_texto(DETALHES) == "~~R$ 19,99~~ **R$ 14,87** (💸 −26%)"
    assert ficha.preco_texto({"price_overview": {"final": 3299, "final_formatted": "R$ 32,99"}}) == "**R$ 32,99**"
    assert ficha.preco_texto({"is_free": True}) == "🆓 Grátis"
    assert ficha.preco_texto({}).startswith("Sem preço")


def test_modos_de_jogo():
    assert ficha.modos_de_jogo(DETALHES) == ["Single-player", "Co-op online"]
    # multiplayer sem dizer o tipo: mostra o genérico
    assert ficha.modos_de_jogo({"categories": [{"id": 1}, {"id": 9}]}) == ["Co-op"]
    assert ficha.modos_de_jogo({"categories": [{"id": 1}]}) == ["Multiplayer"]
    assert ficha.modos_de_jogo({"categories": [{"id": 39}, {"id": 27}]}) == ["Co-op local (tela dividida)", "Crossplay"]


def test_avaliacoes():
    resumo = {"total_reviews": 12345, "total_positive": 11358, "review_score_desc": "Very Positive"}
    assert ficha.avaliacao_texto(resumo) == "Muito positivas · 92% de 12.345 avaliações"
    assert ficha.avaliacao_texto({"total_reviews": 1, "total_positive": 1}) == "100% de 1 avaliação"
    assert ficha.avaliacao_texto(None) == "Ainda sem avaliações"


def test_textos():
    assert ficha.descricao_curta(DETALHES) == ("PEAK é um jogo de escalada cooperativo em que o menor erro "
                                               "pode ser fatal.")
    assert ficha.descricao_curta({"short_description": "a" * 400}, limite=10) == "aaaaaaaaa…"
    assert ficha.generos(DETALHES) == ["Ação", "Aventura", "Indie"]
    assert ficha.lancamento(DETALHES) == "16 jun. 2025"
    assert ficha.lancamento({"release_date": {"coming_soon": True, "date": "2026"}}) == "Em breve (2026)"


def test_nota_do_grupo():
    linhas = [(1, "PEAK", 10, 5), (1, "PEAK", 10, 4), (2, "Peak", 10, None), (3, "Outro", 20, 1)]
    assert ficha.nota_do_grupo(linhas, 10) == (4.5, 2, 2)
    assert ficha.nota_do_grupo(linhas, 30) is None
    assert ficha.nota_do_grupo([(4, "X", 40, None)], 40) == (0.0, 0, 1)


def test_quem_do_servidor():
    db = Database(":memory:")
    for uid in (1, 2, 3):
        db.vincular_steam(uid, f"7656119800000000{uid}", f"u{uid}", uid)
    db.registrar_usuario_servidor(1, 100)
    db.registrar_usuario_servidor(2, 100)
    db.registrar_usuario_servidor(3, 200)          # outro servidor
    assert db.usuarios_com_steam(100) == [1, 2]
    db.salvar_steam_nome("PEAK", 10)
    db.salvar_na_lista(2, ["peak"])
    db.salvar_na_lista(3, ["PEAK"])
    assert db.na_lista_de(100, 10) == [2]
    assert db.na_lista_de(200, 10) == [3]
    db.fechar()
