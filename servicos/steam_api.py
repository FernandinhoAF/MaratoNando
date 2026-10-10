"""Cliente da API oficial da Steam (Steam Web API), usada para ler perfis e bibliotecas.

Precisa de uma chave gratuita: https://steamcommunity.com/dev/apikey
Ela vai no .env como STEAM_API_KEY.
"""

from __future__ import annotations

import aiohttp

URL_API = "https://api.steampowered.com"


class ErroSteamAPI(Exception):
    pass


class SteamAPI:
    def __init__(self, session: aiohttp.ClientSession, chave: str) -> None:
        self.session = session
        self.chave = chave

    async def _get(self, caminho: str, params: dict) -> dict:
        async with self.session.get(URL_API + caminho, params={"key": self.chave, **params}) as r:
            if r.status == 403:
                raise ErroSteamAPI("a Steam recusou a chave da API (confira o STEAM_API_KEY no .env)")
            if r.status != 200:
                raise ErroSteamAPI(f"a Steam respondeu HTTP {r.status}")
            return await r.json(content_type=None)

    async def resolver_nome(self, nome: str) -> str | None:
        """Nome personalizado do perfil (steamcommunity.com/id/NOME) → SteamID64. None se não existe."""
        dados = await self._get("/ISteamUser/ResolveVanityURL/v1/", {"vanityurl": nome})
        resposta = dados.get("response", {})
        return resposta.get("steamid") if resposta.get("success") == 1 else None

    async def perfil(self, steamid: str) -> dict | None:
        """{steamid, personaname, profileurl, communityvisibilitystate, ...} ou None se não existe."""
        dados = await self._get("/ISteamUser/GetPlayerSummaries/v2/", {"steamids": steamid})
        jogadores = dados.get("response", {}).get("players", [])
        return jogadores[0] if jogadores else None

    async def jogos(self, steamid: str) -> list[dict] | None:
        """Jogos da conta: [{appid, name, playtime_forever, ...}].

        None quando os jogos são privados (a Steam devolve a resposta vazia).
        Jogos grátis só aparecem se a pessoa já jogou alguma vez.
        """
        dados = await self._get("/IPlayerService/GetOwnedGames/v1/", {
            "steamid": steamid, "include_appinfo": "1", "include_played_free_games": "1",
        })
        resposta = dados.get("response", {})
        if "games" not in resposta and not resposta.get("game_count"):
            return None if not resposta else []
        return resposta.get("games", [])
