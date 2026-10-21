"""Testes das imagens do Manual da Marca: geram sem erro, no tamanho certo, com qualquer nome."""

from PIL import Image

from cartoes import ALTURA, ESCALA, LARGURA, card_resumo, card_roleta


def abrir(png):
    img = Image.open(png)
    assert img.format == "PNG"
    assert img.size == (LARGURA * ESCALA, ALTURA * ESCALA)
    return img


def test_card_roleta_nomes_curtos_longos_e_acentos():
    abrir(card_roleta(3, "PEAK", "Duda", "3 TÊM · FALTA PRA 1 PESSOA: R$ 14,87", 5))
    abrir(card_roleta(12, "Dale & Dawson Stationery Supplies: The Definitive Edition " * 3,
                      "Fulano com um nome enorme de verdade mesmo", "", 0))
    abrir(card_roleta(1, "Ação São João ÇÃÕÉ", "Zé", "GRÁTIS NA STEAM", 1))
    abrir(card_roleta(1, "Supercalifragilisticexpialidocious", "x"))  # uma palavra só e comprida


def test_card_resumo_vazio_e_cheio():
    abrir(card_resumo("Maratona", [], "0 JOGOS · 5 MINUTOS", []))
    jogos = [(f"Jogo número {i} com nome comprido", f"Pessoa {i}") for i in range(10)]
    abrir(card_resumo("Maratona de Halloween dos Amigos " * 2, jogos, "10 JOGOS · 4H20",
                      [("Bia", 3), ("Caio", 2), ("Duda", 1), ("Ana", 1), ("Zé", 1)]))
