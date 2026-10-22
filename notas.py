"""Notas dos jogos: médias, estrelas e o ranking dos favoritos do grupo.

Módulo puro (sem Discord nem banco), testado em tests/test_notas.py.

O mesmo jogo pode ter sido jogado em várias maratonas, às vezes escrito de jeitos
diferentes ("PEAK", "peak"). Para o ranking, ele conta como um jogo só: pelo número
da Steam (appid) quando o bot reconheceu, ou pelo nome normalizado quando não.
"""

from __future__ import annotations

from dataclasses import dataclass

from radar import normalizar

MINIMO_NOTAS_RANKING = 2  # com uma nota só, um 5 de uma pessoa passaria na frente de tudo


@dataclass(frozen=True)
class NotaJogo:
    nome: str
    media: float
    notas: int   # quantas notas, somando todas as vezes que foi jogado
    vezes: int   # em quantas maratonas o jogo saiu na roleta


def chave_jogo(nome: str, appid: int | None) -> str:
    return f"steam:{appid}" if appid else f"nome:{normalizar(nome)}"


def ranking(linhas: list[tuple[int, str, int | None, int | None]],
            minimo: int = MINIMO_NOTAS_RANKING) -> list[NotaJogo]:
    """Junta as notas de todas as maratonas e ordena os jogos da maior média para a menor.

    linhas: [(jogo_id, nome, appid, nota ou None)], como devolve Database.notas_do_servidor.
    Só entram jogos com pelo menos `minimo` notas. Empate na média: ganha quem tem mais notas.
    """
    grupos: dict[str, dict] = {}
    for jogo_id, nome, appid, nota in linhas:
        g = grupos.setdefault(chave_jogo(nome, appid), {"nome": nome, "ultimo": jogo_id, "jogos": set(), "notas": []})
        g["jogos"].add(jogo_id)
        if jogo_id >= g["ultimo"]:  # mostra o nome usado da última vez que o jogo saiu
            g["nome"], g["ultimo"] = nome, jogo_id
        if nota is not None:
            g["notas"].append(nota)

    saida = [NotaJogo(g["nome"], sum(g["notas"]) / len(g["notas"]), len(g["notas"]), len(g["jogos"]))
             for g in grupos.values() if len(g["notas"]) >= max(minimo, 1)]
    saida.sort(key=lambda j: (-j.media, -j.notas, normalizar(j.nome)))
    return saida


def estrelas(media: float) -> str:
    """4,3 → ★★★★☆ (arredonda para a estrela mais próxima)."""
    cheias = max(0, min(5, int(media + 0.5)))
    return "★" * cheias + "☆" * (5 - cheias)


def numero(media: float) -> str:
    """4.25 → "4,3" (uma casa, vírgula brasileira)."""
    return f"{media:.1f}".replace(".", ",")


def resumo_nota(media: float, quantas: int) -> str:
    """Ex.: "★★★★☆ 4,3 · 3 notas" ou "Ninguém deu nota ainda"."""
    if quantas == 0:
        return "Ninguém deu nota ainda"
    return f"{estrelas(media)} {numero(media)} · {quantas} {'nota' if quantas == 1 else 'notas'}"
