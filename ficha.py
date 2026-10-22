"""Textos da ficha do jogo (/jogo), a partir das respostas da loja da Steam.

Módulo puro (sem Discord nem internet), testado em tests/test_ficha.py.
"""

from __future__ import annotations

import html
import re

from biblioteca import reais

# Categorias da Steam (id → como mostrar). A ordem aqui é a ordem na ficha.
MODOS_DE_JOGO = {
    2: "Single-player",
    38: "Co-op online",
    36: "PvP online",
    39: "Co-op local (tela dividida)",
    37: "PvP local (tela dividida)",
    48: "Co-op em LAN",
    47: "PvP em LAN",
    27: "Crossplay",
}
# Quando o jogo não diz se o multiplayer é online ou local, ao menos isso:
GENERICOS = {9: "Co-op", 1: "Multiplayer"}

# Nomes oficiais da Steam em português para o resumo das avaliações.
AVALIACOES_PT = {
    "Overwhelmingly Positive": "Extremamente positivas",
    "Very Positive": "Muito positivas",
    "Positive": "Positivas",
    "Mostly Positive": "Ligeiramente positivas",
    "Mixed": "Neutras",
    "Mostly Negative": "Ligeiramente negativas",
    "Negative": "Negativas",
    "Very Negative": "Muito negativas",
    "Overwhelmingly Negative": "Extremamente negativas",
}


def modos_de_jogo(detalhes: dict) -> list[str]:
    ids = {c.get("id") for c in detalhes.get("categories", [])}
    modos = [nome for cid, nome in MODOS_DE_JOGO.items() if cid in ids]
    if not any(cid in ids for cid in (38, 36, 39, 37, 48, 47)):  # multiplayer sem dizer de que tipo
        modos += [nome for cid, nome in GENERICOS.items() if cid in ids][:1]
    return modos


def preco_texto(detalhes: dict) -> str:
    if detalhes.get("is_free"):
        return "🆓 Grátis"
    preco = detalhes.get("price_overview")
    if not preco:
        return "Sem preço no Brasil (não está à venda ou ainda não lançou)"
    final = preco.get("final_formatted") or reais(preco.get("final") or 0)
    desconto = int(preco.get("discount_percent") or 0)
    if desconto:
        antes = preco.get("initial_formatted") or reais(preco.get("initial") or 0)
        return f"~~{antes}~~ **{final}** (💸 −{desconto}%)"
    return f"**{final}**"


def avaliacao_texto(resumo: dict | None) -> str:
    """A partir do "query_summary" do /appreviews: "Muito positivas · 92% de 12.345 avaliações"."""
    resumo = resumo or {}
    total = int(resumo.get("total_reviews") or 0)
    if total == 0:
        return "Ainda sem avaliações"
    positivas = int(resumo.get("total_positive") or 0)
    pct = round(100 * positivas / total)
    nome = AVALIACOES_PT.get(resumo.get("review_score_desc", ""), "")
    numero = f"{total:,}".replace(",", ".")
    return f"{nome + ' · ' if nome else ''}{pct}% de {numero} {'avaliação' if total == 1 else 'avaliações'}"


def descricao_curta(detalhes: dict, limite: int = 300) -> str:
    texto = html.unescape(re.sub(r"<[^>]+>", " ", detalhes.get("short_description") or ""))
    texto = " ".join(texto.split())
    return texto if len(texto) <= limite else texto[: limite - 1].rstrip() + "…"


def generos(detalhes: dict, limite: int = 4) -> list[str]:
    return [g.get("description") for g in detalhes.get("genres", []) if g.get("description")][:limite]


def lancamento(detalhes: dict) -> str | None:
    data = detalhes.get("release_date") or {}
    if data.get("coming_soon"):
        return f"Em breve ({data.get('date') or 'sem data'})"
    return data.get("date") or None


def nota_do_grupo(linhas: list[tuple[int, str, int | None, int | None]], appid: int) -> tuple[float, int, int] | None:
    """(média, quantas notas, em quantas maratonas saiu) desse jogo no servidor, ou None se nunca saiu.

    linhas: Database.notas_do_servidor (jogo_id, nome, appid, nota ou None).
    """
    jogos = {j for j, _, a, _ in linhas if a == appid}
    if not jogos:
        return None
    notas = [n for _, _, a, n in linhas if a == appid and n is not None]
    return (sum(notas) / len(notas) if notas else 0.0), len(notas), len(jogos)
