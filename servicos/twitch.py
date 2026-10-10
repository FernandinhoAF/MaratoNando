"""Cliente da API oficial da Twitch (Helix).

Usa o fluxo "client credentials": com o Client ID e o Client Secret da sua
aplicação (dev.twitch.tv/console), o bot pega um token sozinho. Esse token só
lê dados públicos, que é tudo de que o radar precisa.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import aiohttp

URL_TOKEN = "https://id.twitch.tv/oauth2/token"
URL_API = "https://api.twitch.tv/helix"
MAX_POR_PEDIDO = 100  # a Twitch aceita até 100 logins/IDs por chamada


class ErroAPI(Exception):
    pass


def _em_lotes(itens: list, tamanho: int = MAX_POR_PEDIDO):
    for i in range(0, len(itens), tamanho):
        yield itens[i:i + tamanho]


def _rfc3339(momento: datetime) -> str:
    return momento.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TwitchClient:
    def __init__(self, session: aiohttp.ClientSession, client_id: str, client_secret: str) -> None:
        self.session = session
        self.client_id = client_id
        self.client_secret = client_secret
        self._token: str | None = None
        self._expira_em = 0.0

    async def _pegar_token(self, renovar: bool = False) -> str:
        if self._token and not renovar and time.time() < self._expira_em - 60:
            return self._token
        params = {"client_id": self.client_id, "client_secret": self.client_secret,
                  "grant_type": "client_credentials"}
        async with self.session.post(URL_TOKEN, params=params) as r:
            if r.status != 200:
                raise ErroAPI(f"Twitch recusou as credenciais (HTTP {r.status}). Confira o .env.")
            dados = await r.json()
        self._token = dados["access_token"]
        self._expira_em = time.time() + int(dados.get("expires_in", 3600))
        return self._token

    async def _get(self, caminho: str, params: list[tuple[str, str]]) -> dict:
        for tentativa in range(2):
            token = await self._pegar_token(renovar=tentativa > 0)
            headers = {"Client-Id": self.client_id, "Authorization": f"Bearer {token}"}
            async with self.session.get(URL_API + caminho, params=params, headers=headers) as r:
                if r.status == 401 and tentativa == 0:
                    continue  # token venceu: pega outro e tenta de novo
                if r.status != 200:
                    raise ErroAPI(f"Twitch respondeu HTTP {r.status} em {caminho}")
                return await r.json()
        raise ErroAPI("Não consegui autenticar na Twitch")

    async def usuarios(self, logins: list[str]) -> list[dict]:
        """[{id, login, display_name, profile_image_url, ...}] dos canais que existem."""
        saida = []
        for lote in _em_lotes(logins):
            dados = await self._get("/users", [("login", l) for l in lote])
            saida += dados.get("data", [])
        return saida

    async def ao_vivo(self, logins: list[str]) -> list[dict]:
        """Quem está ao vivo agora: [{user_login, game_id, game_name, ...}]."""
        saida = []
        for lote in _em_lotes(logins):
            dados = await self._get("/streams", [("user_login", l) for l in lote] + [("first", "100")])
            saida += [s for s in dados.get("data", []) if s.get("type", "live") == "live"]
        return saida

    async def jogos(self, ids: list[str]) -> list[dict]:
        """[{id, name, box_art_url, igdb_id}] das categorias pedidas."""
        saida = []
        for lote in _em_lotes(ids):
            dados = await self._get("/games", [("id", i) for i in lote])
            saida += dados.get("data", [])
        return saida

    async def clipes(self, broadcaster_id: str, inicio: datetime, fim: datetime) -> list[dict]:
        """Até 100 clipes mais vistos do canal nesse intervalo. Cada clipe tem game_id e created_at."""
        dados = await self._get("/clips", [
            ("broadcaster_id", broadcaster_id), ("started_at", _rfc3339(inicio)),
            ("ended_at", _rfc3339(fim)), ("first", "100"),
        ])
        return dados.get("data", [])
