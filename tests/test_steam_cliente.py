"""A Steam às vezes responde 200 com o corpo vazio. O bot não pode quebrar por isso."""

import asyncio
import json

import pytest

from servicos.steam import ErroSteam, SteamClient


class RespostaFalsa:
    def __init__(self, corpo: str, status: int = 200) -> None:
        self.corpo, self.status = corpo, status

    async def json(self, content_type=None):
        return json.loads(self.corpo)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class SessaoFalsa:
    def __init__(self, corpo: str, status: int = 200) -> None:
        self.corpo, self.status = corpo, status

    def get(self, url, params=None, headers=None):
        return RespostaFalsa(self.corpo, self.status)


def rodar(coro):
    return asyncio.run(coro)


def test_resposta_vazia_vira_erro_da_steam():
    cliente = SteamClient(SessaoFalsa(""))
    with pytest.raises(ErroSteam):
        rodar(cliente.detalhes(620))


def test_resposta_que_nao_e_objeto_vira_erro_da_steam():
    with pytest.raises(ErroSteam):
        rodar(SteamClient(SessaoFalsa("[]")).buscar("Portal"))


def test_avaliacoes_vazias_nao_derrubam_nada():
    assert rodar(SteamClient(SessaoFalsa("")).avaliacoes(620)) is None
    assert rodar(SteamClient(SessaoFalsa("", status=429)).avaliacoes(620)) is None


def test_avaliacoes_normais():
    corpo = json.dumps({"success": 1, "query_summary": {"total_reviews": 10, "total_positive": 9}})
    assert rodar(SteamClient(SessaoFalsa(corpo)).avaliacoes(620)) == {"total_reviews": 10, "total_positive": 9}


def test_radar_continua_sem_as_avaliacoes():
    """O jogo que travava o radar: busca e detalhes funcionam, avaliações voltam vazias."""
    class SessaoMista(SessaoFalsa):
        def get(self, url, params=None, headers=None):
            if "storesearch" in url:
                return RespostaFalsa(json.dumps({"items": [{"id": 620, "name": "Portal 2"}]}))
            if "appdetails" in url:
                return RespostaFalsa(json.dumps({"620": {"success": True, "data": {
                    "name": "Portal 2", "categories": [{"id": 38}], "is_free": False,
                    "price_overview": {"final": 3699, "initial": 3699, "discount_percent": 0}}}}))
            return RespostaFalsa("")  # appreviews vazio

    import servicos.steam as st
    pausa, st.PAUSA = st.PAUSA, 0
    try:
        info = rodar(SteamClient(SessaoMista("")).info("123", "Portal 2", "2026-10-25T00:00:00"))
    finally:
        st.PAUSA = pausa
    assert info.appid == 620
