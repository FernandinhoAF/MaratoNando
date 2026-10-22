"""Estatísticas do grupo (/stats): contas feitas em cima do histórico das maratonas.

Módulo puro (sem Discord nem banco), testado em tests/test_estatisticas.py.
Recebe as linhas "cruas" do banco e devolve os números prontos.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from notas import chave_jogo

# Uma maratona é uma noite. Se alguém esquecer de encerrar e ela ficar aberta por dias,
# conta no máximo 12 horas, para não inflar o total de horas.
MAX_HORAS_POR_MARATONA = 12

# Horário de Brasília (sem horário de verão desde 2019). Decide em que mês cai uma maratona
# que começou, por exemplo, às 22h do dia 31 (que já é dia 1º em UTC).
BRASILIA = timezone(timedelta(hours=-3))
MESES = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")


@dataclass(frozen=True)
class Pessoa:
    user_id: int
    colocou: int  # quantos jogos colocou na roleta (somando todas as maratonas)
    sairam: int   # quantos desses foram sorteados


@dataclass(frozen=True)
class Resumo:
    maratonas: int
    jogos_jogados: int
    horas: float
    desde: str | None  # data ISO da primeira maratona


def _data(iso: str) -> datetime:
    return datetime.fromisoformat(iso)


def resumo(maratonas: list[tuple[str, str | None]],
           jogos: list[tuple[int, str, int | None, int | None]]) -> Resumo:
    """maratonas: [(criada_em, encerrada_em ou None)]; jogos: [(user_id, nome, appid, ordem ou None)].

    As horas contam só maratonas encerradas (a aberta ainda está rolando).
    """
    horas = 0.0
    for criada, encerrada in maratonas:
        if encerrada:
            duracao = (_data(encerrada) - _data(criada)).total_seconds() / 3600
            horas += min(max(duracao, 0.0), MAX_HORAS_POR_MARATONA)
    datas = sorted(c for c, _ in maratonas)
    return Resumo(
        maratonas=len(maratonas),
        jogos_jogados=sum(1 for *_, ordem in jogos if ordem is not None),
        horas=horas,
        desde=datas[0] if datas else None,
    )


def mais_sorteados(jogos: list[tuple[int, str, int | None, int | None]], limite: int = 5) -> list[tuple[str, int]]:
    """Jogos que mais saíram na roleta: [(nome, vezes)], do mais para o menos sorteado.

    O mesmo jogo escrito de jeitos diferentes conta junto (pelo appid da Steam ou pelo nome).
    Empate: o que saiu por último aparece antes (as linhas vêm na ordem em que os jogos entraram).
    """
    contagem: Counter[str] = Counter()
    nome_atual: dict[str, str] = {}
    ultima_vez: dict[str, int] = {}
    for i, (_, nome, appid, ordem) in enumerate(jogos):
        if ordem is None:
            continue
        chave = chave_jogo(nome, appid)
        contagem[chave] += 1
        nome_atual[chave] = nome
        ultima_vez[chave] = i
    ordem_final = sorted(contagem, key=lambda c: (-contagem[c], -ultima_vez[c]))
    return [(nome_atual[c], contagem[c]) for c in ordem_final[:limite]]


def por_pessoa(jogos: list[tuple[int, str, int | None, int | None]], limite: int = 5) -> list[Pessoa]:
    """Quem mais coloca jogo na roleta, com quantos desses saíram."""
    colocou: Counter[int] = Counter()
    sairam: Counter[int] = Counter()
    for user_id, _, _, ordem in jogos:
        colocou[user_id] += 1
        if ordem is not None:
            sairam[user_id] += 1
    pessoas = [Pessoa(u, colocou[u], sairam[u]) for u in colocou]
    pessoas.sort(key=lambda p: (-p.colocou, -p.sairam, p.user_id))
    return pessoas[:limite]


def por_mes(criadas: list[str], hoje: datetime, meses: int = 6) -> list[tuple[str, int]]:
    """Quantas maratonas em cada um dos últimos `meses` meses (do mais antigo ao atual), ex.: [("out/26", 3)]."""
    agora_br = hoje.astimezone(BRASILIA)
    chaves: list[tuple[int, int]] = []
    ano, mes = agora_br.year, agora_br.month
    for _ in range(meses):
        chaves.append((ano, mes))
        mes -= 1
        if mes == 0:
            ano, mes = ano - 1, 12
    chaves.reverse()
    contagem: Counter[tuple[int, int]] = Counter()
    for iso in criadas:
        d = _data(iso).astimezone(BRASILIA)
        contagem[(d.year, d.month)] += 1
    return [(f"{MESES[m - 1]}/{a % 100:02d}", contagem[(a, m)]) for a, m in chaves]


def barras(valores: list[tuple[str, int]], largura: int = 12) -> str:
    """Gráfico de barras em texto, para caber num embed do Discord:

        set/26 ▇▇▇▇ 2
        out/26 ▇▇▇▇▇▇▇▇ 4
    """
    maior = max((v for _, v in valores), default=0)
    linhas = []
    for rotulo, v in valores:
        tamanho = round(v / maior * largura) if maior else 0
        if v and not tamanho:
            tamanho = 1
        linhas.append(f"{rotulo} {'▇' * tamanho}{' ' if v else ''}{v}")
    return "\n".join(linhas)


def horas_texto(horas: float) -> str:
    """3.7 → "3h40"; 0.5 → "30 min"; 27.0 → "27h"."""
    minutos = round(horas * 60)
    h, m = divmod(minutos, 60)
    if h == 0:
        return f"{m} min"
    return f"{h}h{m:02d}" if m else f"{h}h"
