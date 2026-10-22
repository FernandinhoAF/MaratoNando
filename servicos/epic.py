"""Cliente da Epic Games Store: só a lista pública de jogos grátis (não precisa de chave)."""

import aiohttp

from gratis import URL_EPIC


class ErroEpic(Exception):
    pass


class EpicClient:
    def __init__(self, session: aiohttp.ClientSession) -> None:
        self.session = session

    async def jogos_gratis(self) -> dict:
        params = {"locale": "pt-BR", "country": "BR", "allowCountries": "BR"}
        async with self.session.get(URL_EPIC, params=params,
                                    headers={"User-Agent": "MaratoNando (bot de Discord)"}) as r:
            if r.status != 200:
                raise ErroEpic(f"A Epic respondeu {r.status}")
            return await r.json(content_type=None)
