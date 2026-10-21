"""Identidade visual do MaratoNando (Manual da Marca v1, out/2026).

Cores, caminhos das imagens/fontes e as miniaturas do Nando usadas nos embeds.
Tudo o que é "visual" fica aqui, para trocar num lugar só.
"""

from __future__ import annotations

import os
from pathlib import Path

import discord

PASTA_ASSETS = Path(__file__).resolve().parent / "assets"
PASTA_NANDO = PASTA_ASSETS / "nando"
PASTA_FONTES = PASTA_ASSETS / "fontes"

# ---------------------------------------------------------------- paleta (manual, pág. 4)

MAGENTA = (255, 79, 163)    # #FF4FA3 · painel, lista pessoal, sombra dos títulos, regata do Nando
AMARELO = (255, 210, 63)    # #FFD23F · roleta e revelação, nome do jogo, logotipo
VERDE = (69, 227, 154)      # #45E39A · resumo final e confirmações
VERMELHO = (255, 90, 78)    # #FF5A4E · Mural da Vergonha e erros
CIANO = (63, 216, 255)      # #3FD8FF · molduras, rótulos, traços de velocidade, tênis
FUNDO_CRT = (14, 12, 29)    # #0E0C1D · fundo do avatar, banner e imagens
TINTA = (244, 241, 255)     # #F4F1FF · texto claro sobre o fundo CRT

COR_PAINEL = discord.Color.from_rgb(*MAGENTA)
COR_ROLETA = discord.Color.from_rgb(*AMARELO)
COR_FIM = discord.Color.from_rgb(*VERDE)
COR_VERGONHA = discord.Color.from_rgb(*VERMELHO)
COR_DETALHE = discord.Color.from_rgb(*CIANO)  # radar, sugestões, times, Steam, configurações

ASSINATURA = "Caiu, jogou. Sem reclamar."

# ---------------------------------------------------------------- mascote nos embeds

def url_nando(bot: discord.Client, expressao: str = "sorriso") -> str | None:
    """Miniatura redonda do Nando para o autor e o canto dos embeds.

    O Discord só mostra imagens de embed a partir de um link. Com MARCA_URL no .env
    (ex.: o link "raw" da pasta assets do GitHub), usa o busto certinho do manual
    (sorrindo ou surpreso). Sem isso, usa o avatar do próprio bot, que também é o Nando.
    """
    base = os.getenv("MARCA_URL", "").strip().rstrip("/")
    if base:
        return f"{base}/nando/miniatura-{expressao}.png"
    usuario = getattr(bot, "user", None)
    return usuario.display_avatar.url if usuario else None
