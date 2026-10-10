"""Consultas à loja da Steam.

São os mesmos endereços que o site da Steam usa por baixo dos panos. Não exigem
chave, mas não são uma API "oficial": se a Steam mudar algo, este é o arquivo
para ajustar.
"""

from __future__ import annotations

import asyncio

import aiohttp

from radar import InfoJogo, escolher_resultado, montar_info
from sugestao import ItemLoja, ler_resultados

URL_BUSCA = "https://store.steampowered.com/api/storesearch/"
URL_DETALHES = "https://store.steampowered.com/api/appdetails"
URL_AVALIACOES = "https://store.steampowered.com/appreviews/{appid}"
URL_LOJA = "https://store.steampowered.com/search/results/"

PAUSA = 1.0  # segundos entre chamadas, para não ser bloqueado pela Steam


class ErroSteam(Exception):
    pass


class SteamClient:
    def __init__(self, session: aiohttp.ClientSession, pais: str = "BR") -> None:
        self.session = session
        self.pais = pais

    async def _json(self, url: str, params: dict) -> dict:
        async with self.session.get(url, params=params, headers={"User-Agent": "MaratoNando (bot de Discord)"}) as r:
            if r.status != 200:
                raise ErroSteam(f"Steam respondeu HTTP {r.status}")
            return await r.json(content_type=None)

    async def buscar(self, nome: str) -> list[dict]:
        dados = await self._json(URL_BUSCA, {"term": nome, "l": "english", "cc": self.pais})
        return dados.get("items", [])

    async def detalhes(self, appid: int) -> dict | None:
        dados = await self._json(URL_DETALHES, {"appids": str(appid), "cc": self.pais, "l": "english"})
        bloco = dados.get(str(appid)) or {}
        return bloco.get("data") if bloco.get("success") else None

    async def avaliacoes(self, appid: int) -> dict:
        dados = await self._json(URL_AVALIACOES.format(appid=appid),
                                 {"json": "1", "language": "all", "purchase_type": "all", "num_per_page": "0"})
        return dados.get("query_summary", {})

    async def info(self, game_id: str, nome_twitch: str, agora_iso: str) -> InfoJogo:
        """Procura o jogo da Twitch na Steam e junta preço, categorias e avaliações."""
        appid = escolher_resultado(nome_twitch, await self.buscar(nome_twitch))
        if appid is None:
            return montar_info(game_id, nome_twitch, None, None, agora_iso)
        await asyncio.sleep(PAUSA)
        detalhes = await self.detalhes(appid)
        await asyncio.sleep(PAUSA)
        avaliacoes = await self.avaliacoes(appid) if detalhes else None
        return montar_info(game_id, nome_twitch, detalhes, avaliacoes, agora_iso, appid=appid)

    async def busca_loja(self, extra: dict) -> list[ItemLoja]:
        """Uma página da busca da loja: só jogos (category1=998) com co-op online (category3=38)."""
        params = {"infinite": "1", "category1": "998", "category3": "38", "cc": self.pais,
                  "l": "english", "count": "100", "start": "0"}
        params.update({k: str(v) for k, v in extra.items()})
        dados = await self._json(URL_LOJA, params)
        return ler_resultados(dados.get("results_html", ""))

    async def preco(self, appid: int) -> dict | None:
        """Só o preço de um jogo (bem mais leve que os detalhes completos).

        Devolve o price_overview ({final, initial, discount_percent, final_formatted, ...}),
        ou None se o jogo é grátis ou não está à venda no Brasil.
        """
        dados = await self._json(URL_DETALHES, {"appids": str(appid), "cc": self.pais,
                                                "filters": "price_overview"})
        bloco = dados.get(str(appid)) or {}
        if not bloco.get("success") or not isinstance(bloco.get("data"), dict):
            return None
        return bloco["data"].get("price_overview")
