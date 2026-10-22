"""MaratoNando — bot de Discord para maratonas de jogos com roleta.

Para rodar:  python bot.py   (o token fica no arquivo .env; veja o README)
"""

import logging
import os
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv

from db import Database

load_dotenv()  # lê as variáveis do arquivo .env

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("maratonando")


class MaratoNando(commands.Bot):
    def __init__(self) -> None:
        # O bot só usa comandos de barra e botões, então não precisa de intents privilegiadas.
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default(),
                         help_command=None)
        self.db = Database(os.getenv("DB_PATH", "maratonando.db"))

    async def setup_hook(self) -> None:
        await self.load_extension("cogs.maratona")
        await self.load_extension("cogs.radar")
        await self.load_extension("cogs.times")
        await self.load_extension("cogs.limpeza")
        await self.load_extension("cogs.promocoes")
        await self.load_extension("cogs.steam")
        await self.load_extension("cogs.chamar")
        await self.load_extension("cogs.agenda")
        await self.load_extension("cogs.jogar")
        await self.load_extension("cogs.gratis")

        # Com DEV_GUILD_ID, os comandos aparecem na hora só naquele servidor (bom para testar).
        # Sem ele, são registrados para todos os servidores (pode levar alguns minutos).
        dev_guild = os.getenv("DEV_GUILD_ID")
        if dev_guild:
            guild = discord.Object(id=int(dev_guild))
            self.tree.copy_global_to(guild=guild)
            comandos = await self.tree.sync(guild=guild)
            log.info("%d comandos sincronizados no servidor de testes %s", len(comandos), dev_guild)
        else:
            comandos = await self.tree.sync()
            log.info("%d comandos sincronizados globalmente", len(comandos))

    async def on_ready(self) -> None:
        log.info("Logado como %s (id %s) em %d servidor(es)", self.user, self.user.id, len(self.guilds))
        await self.change_presence(activity=discord.Game("/maratona · /ajuda"))

    async def close(self) -> None:
        await super().close()
        self.db.fechar()


def main() -> None:
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        sys.exit("Faltou o DISCORD_TOKEN. Copie .env.example para .env e cole o token do bot lá.")
    MaratoNando().run(token, log_handler=None)


if __name__ == "__main__":
    main()
