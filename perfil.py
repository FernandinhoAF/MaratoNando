"""Contas do /perfil: horas na Steam, jogos mais jogados e gêneros favoritos.

Módulo puro (sem Discord nem internet), testado em tests/test_perfil.py.

Gêneros favoritos = os gêneros dos jogos que a pessoa JOGA, com peso pelas horas
(e não de tudo que ela comprou numa promoção e nunca abriu).
"""

from __future__ import annotations

# Nomes dos gêneros da loja da Steam (em inglês) → português.
GENEROS_PT = {
    "Action": "Ação", "Adventure": "Aventura", "Indie": "Indie", "RPG": "RPG", "Strategy": "Estratégia",
    "Simulation": "Simulação", "Casual": "Casual", "Sports": "Esportes", "Racing": "Corrida",
    "Massively Multiplayer": "MMO", "Early Access": "Acesso antecipado", "Free To Play": "Gratuito para jogar",
    "Free to Play": "Gratuito para jogar",
}
NAO_SAO_GENEROS = {"Early Access", "Free To Play", "Free to Play"}  # dizem como o jogo é vendido, não o tipo
JOGOS_PARA_GENEROS = 20  # olha os gêneros dos 20 jogos mais jogados


def horas(minutos: int) -> str:
    """125 → "2h"; 30 → "30 min"; 123456 → "2.058h"."""
    if minutos < 60:
        return f"{minutos} min"
    return f"{minutos // 60:,}h".replace(",", ".")


def mais_jogados(biblioteca: list[tuple[int, int, int]], limite: int = 5,
                 recentes: bool = False) -> list[tuple[int, int]]:
    """[(appid, minutos)] do mais jogado para o menos. recentes=True usa as últimas 2 semanas.
    Jogos nunca abertos ficam de fora."""
    indice = 2 if recentes else 1
    jogados = [(j[0], j[indice]) for j in biblioteca if j[indice] > 0]
    jogados.sort(key=lambda x: (-x[1], x[0]))
    return jogados[:limite]


def generos_favoritos(biblioteca: list[tuple[int, int, int]], generos: dict[int, list[str]],
                      limite: int = 3) -> list[tuple[str, int]]:
    """[(gênero em português, % do tempo)] dos gêneros mais jogados, pelo tempo dos jogos mais jogados.

    Um jogo com vários gêneros conta inteiro para cada um (a % é sobre o tempo desses jogos)."""
    top = mais_jogados(biblioteca, JOGOS_PARA_GENEROS)
    total = sum(m for a, m in top if generos.get(a))
    if total == 0:
        return []
    soma: dict[str, int] = {}
    for appid, minutos in top:
        for g in generos.get(appid, []):
            if g in NAO_SAO_GENEROS:
                continue
            nome = GENEROS_PT.get(g, g)
            soma[nome] = soma.get(nome, 0) + minutos
    ordem = sorted(soma.items(), key=lambda x: (-x[1], x[0]))[:limite]
    return [(nome, round(100 * m / total)) for nome, m in ordem]
