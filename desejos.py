"""Lista de desejos do grupo: junta o que cada pessoa quer (lista do bot + lista de desejos da Steam).

Módulo puro (sem Discord nem internet), testado em tests/test_desejos.py.
"""

from __future__ import annotations

from dataclasses import dataclass

MINIMO_GRUPO = 2  # a partir de quantas pessoas um jogo vira "desejo do grupo"


@dataclass(frozen=True)
class Desejo:
    appid: int
    querem: tuple[int, ...]      # quem quer e ainda não tem
    ja_tem: tuple[int, ...]      # quem já tem (das pessoas com Steam vinculada)
    da_steam: bool               # veio (também) da lista de desejos da Steam

    @property
    def do_grupo(self) -> bool:
        return len(self.querem) >= MINIMO_GRUPO


def agrupar(linhas: list[tuple[int, int, str]], bibliotecas: dict[int, set[int]],
            descontos: dict[int, int] | None = None) -> list[Desejo]:
    """Ordena os jogos do mais desejado para o menos.

    linhas: Database.desejos_do_servidor → [(user_id, appid, "lista" ou "steam")].
    bibliotecas: {user_id: {appids}} de quem tem a Steam vinculada; quem já tem o jogo sai de "querem"
    (a lista pessoal às vezes fica com jogo que a pessoa já comprou).
    descontos: {appid: % de desconto agora}; empate em quantas pessoas querem → maior desconto primeiro.
    Jogos que ninguém mais quer (todo mundo que queria já tem) ficam de fora.
    """
    descontos = descontos or {}
    querem: dict[int, set[int]] = {}
    steam: set[int] = set()
    for user_id, appid, origem in linhas:
        querem.setdefault(appid, set()).add(user_id)
        if origem == "steam":
            steam.add(appid)
    saida = []
    for appid, pessoas in querem.items():
        donos = {u for u, jogos in bibliotecas.items() if appid in jogos}
        faltam = pessoas - donos
        if not faltam:
            continue
        saida.append(Desejo(appid, tuple(sorted(faltam)), tuple(sorted(donos)), appid in steam))
    saida.sort(key=lambda d: (-len(d.querem), -descontos.get(d.appid, 0), d.appid))
    return saida
