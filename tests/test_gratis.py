"""Testes dos jogos grátis (Epic e Steam), com um pedaço da resposta real da Epic (22/10/2026)."""

from datetime import datetime, timezone

from db import Database
from gratis import ler_epic, ler_steam
from sugestao import ItemLoja


def item(titulo, id_, slug, preco, original, atuais=None, futuras=None, product_slug=None, page_type="productHome"):
    promos = None
    if atuais is not None or futuras is not None:
        promos = {"promotionalOffers": [{"promotionalOffers": atuais or []}] if atuais else [],
                  "upcomingPromotionalOffers": [{"promotionalOffers": futuras or []}] if futuras else []}
    return {
        "title": titulo, "id": id_, "productSlug": product_slug, "offerType": "BASE_GAME",
        "catalogNs": {"mappings": [{"pageSlug": slug, "pageType": "productHome"}]},
        "offerMappings": [{"pageSlug": slug, "pageType": page_type}],
        "price": {"totalPrice": {"discountPrice": preco, "originalPrice": original,
                                 "fmtPrice": {"originalPrice": f"R$ {original / 100:.2f}".replace(".", ","),
                                              "discountPrice": "0" if preco == 0 else "R$ x"}}},
        "promotions": promos,
        "keyImages": [{"type": "OfferImageTall", "url": "https://img/alta.png"},
                      {"type": "OfferImageWide", "url": "https://img/larga.png"}],
    }


def promo(inicio, fim, pct):
    return {"startDate": inicio, "endDate": fim,
            "discountSetting": {"discountType": "PERCENTAGE", "discountPercentage": pct}}


EPIC = {"data": {"Catalog": {"searchStore": {"elements": [
    item("Bad Cheese", "6a84", "bad-cheese-4256d9", 0, 3499,
         atuais=[promo("2026-10-15T15:00:00.000Z", "2026-10-22T15:00:00.000Z", 0)]),
    item("Agent A: O enigma disfarçado", "db12", "agent-a-a-puzzle-in-disguise-42fe5f", 0, 5299,
         atuais=[promo("2026-10-15T15:00:00.000Z", "2026-10-22T15:00:00.000Z", 0)]),
    item("Sker Ritual", "6aab", "sker-ritual-aac702", 6699, 6699,
         futuras=[promo("2026-10-22T15:00:00.000Z", "2026-10-29T15:00:00.000Z", 0)]),
    item("Eternal Threads", "921e", "eternal-threads-197169", 1059, 5299,     # 80% off: não é grátis
         atuais=[promo("2026-10-19T15:00:00.000Z", "2026-11-02T16:00:00.000Z", 20)]),
    item("Them's Fightin' Herds", "1f72", "thems-fightin-herds", 3799, 3799,  # sem promoção (promotions: null)
         product_slug="thems-fightin-herds/home"),
]}}}}

QUINTA_MADRUGADA = datetime(2026, 10, 22, 4, 0, tzinfo=timezone.utc)


def test_epic_gratis_agora_e_em_breve():
    agora, em_breve = ler_epic(EPIC, QUINTA_MADRUGADA)
    assert [b.nome for b in agora] == ["Bad Cheese", "Agent A: O enigma disfarçado"]
    b = agora[0]
    assert b.url == "https://store.epicgames.com/pt-BR/p/bad-cheese-4256d9"
    assert b.imagem == "https://img/larga.png"          # prefere a imagem larga
    assert b.preco_original == "R$ 34,99"
    assert (b.inicio, b.fim) == ("2026-10-15T15:00:00+00:00", "2026-10-22T15:00:00+00:00")
    assert b.chave == "epic:6a84:2026-10-15T15:00:00+00:00"
    assert [b.nome for b in em_breve] == ["Sker Ritual"]


def test_epic_depois_que_vira_a_semana():
    depois = datetime(2026, 10, 22, 16, 0, tzinfo=timezone.utc)
    agora, em_breve = ler_epic(EPIC, depois)
    # Bad Cheese acabou; Sker Ritual começou, mas a resposta antiga ainda não tinha o preço 0: só conta
    # quando a Epic mostra o preço zerado (evita anunciar antes da hora)
    assert agora == [] and [b.nome for b in em_breve] == ["Sker Ritual"]


def test_epic_resposta_vazia_ou_estranha():
    assert ler_epic({}, QUINTA_MADRUGADA) == ([], [])
    assert ler_epic({"data": {"Catalog": None}}, QUINTA_MADRUGADA) == ([], [])


def test_steam_so_100_off():
    itens = [
        ItemLoja(1, "Grátis Para Sempre", 0, "Free", 100, 0.9, 1000, (), None),
        ItemLoja(2, "Free to Play", 0, "Free to Play", 0, 0.8, 50000, (), None),
        ItemLoja(3, "Quase Grátis", 199, "R$ 1,99", 90, 0.7, 10, (), None),
    ]
    brindes = ler_steam(itens)
    assert [b.nome for b in brindes] == ["Grátis Para Sempre"]
    assert brindes[0].url == "https://store.steampowered.com/app/1/" and brindes[0].chave == "steam:1:"


def test_banco_nao_avisa_duas_vezes():
    db = Database(":memory:")
    db.definir_canal_bot(10, 99)
    db.definir_canal_bot(20, 98)
    db.definir_avisar_gratis(20, False)
    assert db.servidores_para_gratis() == [(10, 99)]
    assert db.gratis_novos(10, ["a", "b"]) == ["a", "b"]
    db.marcar_gratis_avisados(10, ["a"])
    assert db.gratis_novos(10, ["a", "b"]) == ["b"]
    assert db.gratis_novos(20, ["a"]) == ["a"]          # cada servidor tem a sua memória
    assert db.avisar_gratis(10) is True and db.avisar_gratis(20) is False
    db.fechar()
