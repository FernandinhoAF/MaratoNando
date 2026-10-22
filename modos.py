"""Modos da roleta: quais jogos podem sair no sorteio.

  • livre      — qualquer jogo da roleta (o normal).
  • todos_tem  — ninguém precisa comprar: todo mundo (com Steam vinculada) já tem, ou é grátis.
  • ate_preco  — quem não tem paga no máximo R$ X (quem já tem, ou jogo grátis, sempre passa).

Os jogos que não passam continuam na roleta: só não saem enquanto o modo estiver ligado.
Jogo que o bot não conseguiu conferir na Steam (fora da Steam, sem preço) também fica de fora,
porque o modo é uma promessa de que ninguém vai ser pego de surpresa pagando.

Módulo puro (sem Discord nem internet), testado em tests/test_modos.py.
"""

from __future__ import annotations

from biblioteca import Opcao, reais

MODOS = ("livre", "todos_tem", "ate_preco")


def descricao(modo: str, preco_centavos: int | None = None) -> str:
    if modo == "todos_tem":
        return "✅ Ninguém precisa comprar (todo mundo tem, ou é grátis)"
    if modo == "ate_preco":
        return f"💸 Só jogos de até {reais(preco_centavos or 0)} para quem não tem"
    return "🎲 Livre (qualquer jogo da roleta)"


def passa(modo: str, preco_centavos: int | None, opcao: Opcao | None) -> bool:
    """O jogo pode sair no sorteio neste modo? opcao=None = não deu para conferir na Steam."""
    if modo == "livre":
        return True
    if opcao is None:
        return False
    if opcao.todos_tem or opcao.app.gratis:
        return True
    if modo == "ate_preco":
        preco = opcao.app.preco_centavos
        return preco is not None and preco <= (preco_centavos or 0)
    return False


def validar(modo: str, preco_reais: int | None) -> int | None:
    """Confere a escolha e devolve o preço em centavos (ou None). Levanta ValueError com a explicação."""
    if modo not in MODOS:
        raise ValueError("Modo desconhecido.")
    if modo == "ate_preco":
        if preco_reais is None:
            raise ValueError("No modo **só até R$ X**, diga o preço máximo em `preco` (ex.: 20).")
        return preco_reais * 100
    return None
