"""Jogos grátis para resgatar (de graça para sempre): Epic Games (o grátis da semana) e Steam (100% off).

Módulo puro (sem Discord nem internet), testado em tests/test_gratis.py.

Epic: o endereço público freeGamesPromotions lista os jogos em destaque. Um jogo está grátis quando
tem uma promoção ativa com "discountPercentage": 0 (é assim que a Epic marca o 100% de desconto)
e o preço com desconto é 0. As promoções "upcoming" com 0% são os grátis da próxima semana.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sugestao import ItemLoja

URL_EPIC = "https://store-site-backend-static.ak.epicgames.com/freeGamesPromotions"
LOJA_EPIC = "https://store.epicgames.com/pt-BR/p/{slug}"


@dataclass(frozen=True)
class Brinde:
    loja: str              # "epic" ou "steam"
    id: str                # id do jogo na loja
    nome: str
    url: str
    imagem: str | None
    preco_original: str | None  # "R$ 34,99"
    inicio: str | None     # ISO (UTC); None = a loja não diz
    fim: str | None

    @property
    def chave(self) -> str:
        """Identifica ESTA promoção (o mesmo jogo pode voltar a ficar grátis outro dia)."""
        return f"{self.loja}:{self.id}:{self.inicio or ''}"


def _data(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def _iso(iso: str) -> str:
    return _data(iso).astimezone(timezone.utc).isoformat(timespec="seconds")


def _slug(item: dict) -> str | None:
    """O pedaço do link da página do jogo na Epic (os campos mudam de jogo para jogo)."""
    for mapa in (item.get("offerMappings") or []) + ((item.get("catalogNs") or {}).get("mappings") or []):
        if mapa.get("pageType") == "productHome" and mapa.get("pageSlug"):
            return mapa["pageSlug"]
    slug = (item.get("productSlug") or "").removesuffix("/home")
    return slug or None


def _imagem(item: dict) -> str | None:
    imagens = {i.get("type"): i.get("url") for i in item.get("keyImages") or []}
    for tipo in ("OfferImageWide", "DieselStoreFrontWide", "featuredMedia", "Thumbnail", "OfferImageTall"):
        if imagens.get(tipo):
            return imagens[tipo]
    return None


def _promos_gratis(blocos: list | None) -> list[dict]:
    """As promoções de 100% (a Epic escreve "discountPercentage": 0) dentro da lista da Epic."""
    saida = []
    for bloco in blocos or []:
        for p in bloco.get("promotionalOffers") or []:
            if (p.get("discountSetting") or {}).get("discountPercentage") == 0:
                saida.append(p)
    return saida


def ler_epic(dados: dict, agora: datetime) -> tuple[list[Brinde], list[Brinde]]:
    """(grátis agora, grátis em breve) a partir da resposta da Epic."""
    elementos = (((dados.get("data") or {}).get("Catalog") or {}).get("searchStore") or {}).get("elements") or []
    agora_lista, em_breve = [], []
    for item in elementos:
        slug = _slug(item)
        if not slug or not item.get("title"):
            continue
        promos = item.get("promotions") or {}
        preco = ((item.get("price") or {}).get("totalPrice") or {})
        original = (preco.get("fmtPrice") or {}).get("originalPrice")
        base = dict(loja="epic", id=str(item.get("id")), nome=item["title"], url=LOJA_EPIC.format(slug=slug),
                    imagem=_imagem(item), preco_original=original if original not in (None, "0") else None)
        for p in _promos_gratis(promos.get("promotionalOffers")):
            inicio, fim = _data(p["startDate"]), _data(p["endDate"])
            if inicio <= agora < fim and preco.get("discountPrice") == 0:
                agora_lista.append(Brinde(**base, inicio=_iso(p["startDate"]), fim=_iso(p["endDate"])))
        for p in _promos_gratis(promos.get("upcomingPromotionalOffers")):
            if _data(p["endDate"]) > agora:
                em_breve.append(Brinde(**base, inicio=_iso(p["startDate"]), fim=_iso(p["endDate"])))
    em_breve.sort(key=lambda b: b.inicio or "")
    return agora_lista, em_breve


def ler_steam(itens: list[ItemLoja]) -> list[Brinde]:
    """Da busca da Steam (promoções que custam R$ 0): só o que é 100% off (de graça para sempre),
    não os free-to-play de sempre."""
    saida = []
    for i in itens:
        if i.desconto == 100 and (i.preco_centavos or 0) == 0:
            saida.append(Brinde("steam", str(i.appid), i.nome, i.url,
                                f"https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{i.appid}/header.jpg",
                                None, None, None))
    return saida
