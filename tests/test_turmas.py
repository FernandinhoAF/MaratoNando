"""Testes do "Quem topa?" (turmas de jogos)."""

from db import Database
import turmas

G, ANA, BIA, CAIO = 1, 100, 200, 300


def test_quem_pergunta_ja_topa_e_a_turma_e_reaproveitada():
    db = Database(":memory:")
    t, nova = db.abrir_turma(G, "PEAK", 3527290, ANA, "sábado à noite")
    assert nova and db.respostas_turma(t) == {"topo": [ANA], "talvez": [], "passo": []}
    # Outra pessoa pergunta do mesmo jogo (nome diferente na digitação): mesma turma, votos mantidos
    t2, nova2 = db.abrir_turma(G, "peak", None, BIA)
    assert (t2, nova2) == (t, False)
    assert db.respostas_turma(t)["topo"] == [ANA, BIA]
    assert db.turma(t)["quando"] == "sábado à noite"  # sem "quando" novo, o antigo fica
    db.fechar()


def test_achar_pelo_jogo_da_steam_ou_pelo_nome():
    db = Database(":memory:")
    t, _ = db.abrir_turma(G, "PEAK", 3527290, ANA)
    assert db.turma_do_jogo(G, "Nome qualquer", 3527290)["id"] == t   # mesmo appid
    assert db.turma_do_jogo(G, "  PEAK ", None)["id"] == t           # mesmo nome
    assert db.turma_do_jogo(G, "REPO", 999) is None
    assert db.turma_do_jogo(2, "PEAK", 3527290) is None              # outro servidor
    db.fechar()


def test_mudar_de_resposta_e_fechar():
    db = Database(":memory:")
    t, _ = db.abrir_turma(G, "PEAK", None, ANA)
    db.responder_turma(t, BIA, "talvez")
    db.responder_turma(t, CAIO, "topo")
    db.responder_turma(t, BIA, "topo")   # mudou de ideia
    db.responder_turma(t, CAIO, "passo")
    assert db.respostas_turma(t) == {"topo": [ANA, BIA], "talvez": [], "passo": [CAIO]}
    assert db.turmas_abertas(G)[0][1] == 2
    assert db.fechar_turma(t, "bora") and not db.fechar_turma(t, "maratona")  # fecha uma vez só
    assert db.turma(t)["fechada_por"] == "bora"
    assert db.turma_do_jogo(G, "PEAK") is None and db.turmas_abertas(G) == []
    # Depois de jogado, perguntar de novo abre uma turma nova, do zero
    t3, nova = db.abrir_turma(G, "PEAK", None, CAIO)
    assert nova and t3 != t and db.respostas_turma(t3)["topo"] == [CAIO]
    db.fechar()


def test_turmas_abertas_em_ordem_de_quem_tem_mais_gente():
    db = Database(":memory:")
    a, _ = db.abrir_turma(G, "A", None, ANA)
    b, _ = db.abrir_turma(G, "B", None, ANA)
    db.responder_turma(b, BIA, "topo")
    assert [(t["nome"], n) for t, n in db.turmas_abertas(G)] == [("B", 2), ("A", 1)]
    db.fechar()


def test_textos():
    r = {"topo": [ANA, BIA], "talvez": [], "passo": [CAIO]}
    assert turmas.linhas_respostas(r) == [f"🙋 **Topariam (2)**: <@{ANA}> <@{BIA}>", "🤔 **Talvez (0)**: —",
                                         f"🙅 **Não curtem (1)**: <@{CAIO}>"]
    assert turmas.para_marcar(r, tirar=ANA) == [BIA]          # quem chama não é marcado; passo nunca
    assert turmas.resumo_painel([]) is None
    assert turmas.resumo_painel([("A", 5), ("B", 3), ("C", 2), ("D", 1)]) == \
        "**A** (5 🙋) · **B** (3 🙋) · **C** (2 🙋) · e mais 1"


DETALHES_PEAK = {
    "name": "PEAK", "short_description": "Escalem a <b>montanha</b> juntos &amp; sobrevivam.",
    "genres": [{"description": "Aventura"}, {"description": "Indie"}],
    "categories": [{"id": 2}, {"id": 38}], "is_free": False,
    "price_overview": {"final_formatted": "R$ 18,99", "discount_percent": 0},
    "release_date": {"date": "16 jun. 2025", "coming_soon": False},
    "header_image": "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/3527290/abc/header.jpg",
}


def test_sobre_o_jogo():
    avaliacoes = {"total_reviews": 1000, "total_positive": 960, "review_score_desc": "Overwhelmingly Positive"}
    assert turmas.linhas_sobre(DETALHES_PEAK, avaliacoes, (1, 4)) == [
        "🏷️ Aventura · Indie",
        "👥 1 a 4 jogadores · Single-player · Co-op online",
        "💰 **R$ 18,99** · 👍 Extremamente positivas · 96% de 1.000 avaliações",
        "📅 Lançamento: 16 jun. 2025",
    ]
    # Sem avaliações (a Steam não mandou) e sem nº de jogadores: o resto aparece igual
    assert turmas.linhas_sobre({"is_free": True}, None, None) == ["💰 🆓 Grátis"]


def test_ficha_guardada_na_turma():
    db = Database(":memory:")
    t, _ = db.abrir_turma(G, "PEAK", 3527290, ANA)
    assert db.turma(t)["descricao"] is None
    db.definir_ficha_turma(t, "Escalem a montanha.", "🏷️ Aventura", "https://cdn/peak/header.jpg")
    db.abrir_turma(G, "PEAK", 3527290, BIA)  # perguntar de novo não apaga a ficha
    assert tuple(db.turma(t)[c] for c in ("descricao", "ficha", "capa")) == \
        ("Escalem a montanha.", "🏷️ Aventura", "https://cdn/peak/header.jpg")
    db.fechar()
