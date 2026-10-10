"""Camada de dados do MaratoNando.

Tudo o que o bot precisa lembrar fica num arquivo SQLite: as maratonas, os
jogos que cada pessoa colocou na roleta e as reclamações do Mural da Vergonha.
Assim, se o bot reiniciar no meio da noite, a maratona continua de onde parou.

Este módulo não sabe nada de Discord: só recebe e devolve números (IDs) e
textos. Isso deixa as regras fáceis de testar (veja tests/test_db.py).
"""

from __future__ import annotations

import random
import re
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from radar import JANELA_DIAS, InfoJogo

TAM_MAX_NOME = 80  # caracteres por nome de jogo
COOLDOWN_RECLAMACAO = 30  # segundos entre duas denúncias iguais (anti-spam)
MAX_LISTA = 100  # jogos na lista pessoal de cada pessoa

# SystemRandom usa a fonte de aleatoriedade do sistema operacional:
# ninguém consegue "prever" o próximo sorteio.
_rng = random.SystemRandom()

SCHEMA = """
CREATE TABLE IF NOT EXISTS maratonas (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id         INTEGER NOT NULL,
    channel_id       INTEGER NOT NULL,
    panel_message_id INTEGER,
    host_id          INTEGER NOT NULL,
    nome             TEXT    NOT NULL,
    limite           INTEGER,                         -- jogos por pessoa (NULL = sem limite)
    status           TEXT    NOT NULL DEFAULT 'aberta', -- 'aberta' ou 'encerrada'
    criada_em        TEXT    NOT NULL,
    encerrada_em     TEXT
);

-- No máximo UMA maratona aberta por servidor.
CREATE UNIQUE INDEX IF NOT EXISTS uma_aberta_por_servidor
    ON maratonas (guild_id) WHERE status = 'aberta';

CREATE INDEX IF NOT EXISTS idx_maratonas_painel ON maratonas (panel_message_id);

CREATE TABLE IF NOT EXISTS jogos (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    maratona_id   INTEGER NOT NULL REFERENCES maratonas (id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL,
    nome          TEXT    NOT NULL,
    adicionado_em TEXT    NOT NULL,
    sorteado_em   TEXT,
    ordem         INTEGER  -- 1º, 2º, 3º jogo sorteado... (NULL = ainda na roleta)
);

CREATE INDEX IF NOT EXISTS idx_jogos_maratona ON jogos (maratona_id);

CREATE TABLE IF NOT EXISTS reclamacoes (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    maratona_id    INTEGER NOT NULL REFERENCES maratonas (id) ON DELETE CASCADE,
    user_id        INTEGER NOT NULL,  -- quem reclamou
    denunciante_id INTEGER NOT NULL,  -- quem dedurou
    jogo_id        INTEGER REFERENCES jogos (id) ON DELETE SET NULL,
    criada_em      TEXT    NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reclamacoes_maratona ON reclamacoes (maratona_id);

-- Lista pessoal: jogos que a pessoa vai guardando para as próximas maratonas.
-- É da pessoa (não do servidor), então vale em qualquer servidor onde o bot estiver.
CREATE TABLE IF NOT EXISTS lista_pessoal (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id  INTEGER NOT NULL,
    nome     TEXT    NOT NULL,
    chave    TEXT    NOT NULL,  -- nome em minúsculas, para achar repetidos
    salvo_em TEXT    NOT NULL,
    UNIQUE (user_id, chave)
);

-- ===== Radar de jogos =====

-- Streamers que cada servidor acompanha.
CREATE TABLE IF NOT EXISTS radar_streamers (
    guild_id      INTEGER NOT NULL,
    login         TEXT    NOT NULL,  -- nome do canal na Twitch, minúsculo (ex.: alanzoka)
    twitch_id     TEXT    NOT NULL,
    nome          TEXT    NOT NULL,  -- nome de exibição (ex.: alanzoka)
    adicionado_em TEXT    NOT NULL,
    PRIMARY KEY (guild_id, login)
);

-- Configurações do radar em cada servidor.
CREATE TABLE IF NOT EXISTS radar_config (
    guild_id        INTEGER PRIMARY KEY,
    canal_alertas   INTEGER,  -- onde avisar quando aparecer jogo novo (NULL = sem avisos)
    preco_max       INTEGER   -- em centavos (NULL = padrão)
);

-- "Fulano jogou tal jogo em tal dia" (vem dos clipes e de quando ele está ao vivo).
CREATE TABLE IF NOT EXISTS radar_vistos (
    login     TEXT NOT NULL,
    game_id   TEXT NOT NULL,
    dia       TEXT NOT NULL,  -- AAAA-MM-DD
    game_name TEXT NOT NULL,
    PRIMARY KEY (login, game_id, dia)
);

-- O que a Steam diz de cada jogo (guardado para não perguntar toda hora).
CREATE TABLE IF NOT EXISTS radar_jogos (
    game_id        TEXT PRIMARY KEY,
    twitch_nome    TEXT    NOT NULL,
    appid          INTEGER,
    nome           TEXT,
    gratis         INTEGER NOT NULL,
    preco_centavos INTEGER,
    preco_texto    TEXT,
    desconto       INTEGER NOT NULL,
    multiplayer    INTEGER NOT NULL,
    coop           INTEGER NOT NULL,
    review_pct     REAL,
    review_total   INTEGER NOT NULL,
    imagem         TEXT,
    verificado_em  TEXT    NOT NULL
);

-- Jogos que já foram avisados em cada servidor (para não avisar duas vezes).
CREATE TABLE IF NOT EXISTS radar_avisados (
    guild_id INTEGER NOT NULL,
    game_id  TEXT    NOT NULL,
    PRIMARY KEY (guild_id, game_id)
);

-- ===== Limpeza do chat =====

-- Canal próprio do bot e quanto tempo esperar para apagar mensagens fora dele.
CREATE TABLE IF NOT EXISTS limpeza_config (
    guild_id  INTEGER PRIMARY KEY,
    canal_bot INTEGER,  -- mensagens neste canal nunca são apagadas
    minutos   INTEGER   -- NULL = padrão; 0 = nunca apagar
);

-- Mensagens do bot agendadas para sumir (fica no banco para sobreviver a reinícios).
CREATE TABLE IF NOT EXISTS limpeza_agenda (
    message_id INTEGER PRIMARY KEY,
    channel_id INTEGER NOT NULL,
    guild_id   INTEGER NOT NULL,
    apagar_em  TEXT    NOT NULL
);

-- ===== Promoções dos jogos das listas =====

-- Em quais servidores cada pessoa usa o bot (para saber onde avisar das promoções dela).
CREATE TABLE IF NOT EXISTS usuarios_servidores (
    user_id  INTEGER NOT NULL,
    guild_id INTEGER NOT NULL,
    PRIMARY KEY (user_id, guild_id)
);

-- Nome de jogo (como está nas listas) → jogo na Steam.
CREATE TABLE IF NOT EXISTS steam_nomes (
    chave         TEXT PRIMARY KEY,  -- nome em minúsculas
    nome          TEXT NOT NULL,
    appid         INTEGER,           -- NULL = não achei na Steam
    verificado_em TEXT NOT NULL
);

-- Promoções em andamento (some daqui quando a promoção acaba).
CREATE TABLE IF NOT EXISTS promocoes (
    appid          INTEGER PRIMARY KEY,
    nome           TEXT    NOT NULL,
    desconto       INTEGER NOT NULL,
    preco_texto    TEXT,
    original_texto TEXT,
    desde          TEXT    NOT NULL
);

-- ===== Jogadores e Steam =====

-- Quem entrou na maratona sem colocar jogo (chegou no meio, por exemplo).
CREATE TABLE IF NOT EXISTS maratona_extras (
    maratona_id INTEGER NOT NULL REFERENCES maratonas (id) ON DELETE CASCADE,
    user_id     INTEGER NOT NULL,
    PRIMARY KEY (maratona_id, user_id)
);

-- Conta da Steam de cada pessoa.
CREATE TABLE IF NOT EXISTS steam_contas (
    user_id       INTEGER PRIMARY KEY,
    steamid       TEXT    NOT NULL,  -- ID de 17 dígitos (7656119...)
    nome          TEXT,              -- nome do perfil na Steam
    publico       INTEGER NOT NULL DEFAULT 0,  -- 1 = dá para ver os jogos
    total_jogos   INTEGER NOT NULL DEFAULT 0,
    vinculado_por INTEGER NOT NULL,
    atualizado_em TEXT               -- última vez que a biblioteca foi lida (NULL = nunca)
);

-- Jogos que cada pessoa tem na Steam.
CREATE TABLE IF NOT EXISTS steam_biblioteca (
    user_id INTEGER NOT NULL,
    appid   INTEGER NOT NULL,
    PRIMARY KEY (user_id, appid)
);

-- O que a loja diz de cada jogo (multiplayer? preço?), guardado para não perguntar toda hora.
CREATE TABLE IF NOT EXISTS steam_apps (
    appid          INTEGER PRIMARY KEY,
    nome           TEXT    NOT NULL,
    gratis         INTEGER NOT NULL,
    preco_centavos INTEGER,
    preco_texto    TEXT,
    desconto       INTEGER NOT NULL DEFAULT 0,
    multiplayer    INTEGER NOT NULL,
    coop           INTEGER NOT NULL,
    verificado_em  TEXT    NOT NULL
);
"""

# Colunas criadas depois da primeira versão: adicionadas sozinhas em bancos antigos.
MIGRACOES = [
    ("limpeza_config", "avisar_promocoes", "INTEGER NOT NULL DEFAULT 1"),
]

MINUTOS_LIMPEZA_PADRAO = 10
RECHECAR_NOME_STEAM_DIAS = 14

RECHECAR_STEAM_DIAS = 3       # preço/promoção mudam: confere de novo a cada 3 dias
RECHECAR_SEM_STEAM_DIAS = 14  # jogo que não achei na Steam: tenta de novo em 2 semanas


def agora() -> str:
    """Data e hora atuais em UTC, no formato ISO (ex.: 2026-10-10T01:30:00+00:00)."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class ErroMaratona(Exception):
    """Regra quebrada. A mensagem já vem pronta para mostrar a quem usou o bot."""


@dataclass(frozen=True)
class Maratona:
    id: int
    guild_id: int
    channel_id: int
    panel_message_id: int | None
    host_id: int
    nome: str
    limite: int | None
    status: str
    criada_em: str
    encerrada_em: str | None

    @property
    def aberta(self) -> bool:
        return self.status == "aberta"


@dataclass(frozen=True)
class Jogo:
    id: int
    maratona_id: int
    user_id: int
    nome: str
    adicionado_em: str
    sorteado_em: str | None
    ordem: int | None

    @property
    def jogado(self) -> bool:
        return self.ordem is not None


@dataclass(frozen=True)
class ItemLista:
    id: int
    user_id: int
    nome: str
    chave: str
    salvo_em: str


@dataclass(frozen=True)
class Participante:
    user_id: int
    total: int    # quantos jogos colocou
    jogados: int  # quantos desses já saíram


@dataclass(frozen=True)
class Sorteio:
    jogo: Jogo
    numero: int     # este é o N-ésimo jogo da noite
    restantes: int  # quantos sobraram na roleta depois deste


# ---------------------------------------------------------------- nomes

_MARCADOR_LISTA = re.compile(r"^(?:[-*•]+|\d+[.)])\s+")


def limpar_nome(nome: str) -> str:
    """Tira espaços sobrando e marcadores de lista ("- ", "1. ") e corta no tamanho máximo."""
    nome = " ".join(nome.split())
    nome = _MARCADOR_LISTA.sub("", nome).strip()
    return nome[:TAM_MAX_NOME]


def separar_nomes(texto: str) -> list[str]:
    """Transforma o texto do formulário (um jogo por linha) numa lista de nomes."""
    nomes = (limpar_nome(linha) for linha in texto.splitlines())
    return [n for n in nomes if n]


def chave_nome(nome: str) -> str:
    """Versão do nome usada para achar repetidos ("HADES" == "hades")."""
    return nome.casefold()


def _chave(nome: str) -> str:
    """Versão do nome usada para achar repetidos ("HADES" == "hades")."""
    return nome.casefold()


# ---------------------------------------------------------------- banco

class Database:
    def __init__(self, caminho: str | Path = "maratonando.db") -> None:
        self.conn = sqlite3.connect(str(caminho))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.executescript(SCHEMA)
        self._migrar()
        self.conn.commit()

    def _migrar(self) -> None:
        """Atualiza bancos criados por versões antigas do bot, sem perder nada."""
        for tabela, coluna, tipo in MIGRACOES:
            colunas = {r["name"] for r in self.conn.execute(f"PRAGMA table_info({tabela})")}
            if coluna not in colunas:
                self.conn.execute(f"ALTER TABLE {tabela} ADD COLUMN {coluna} {tipo}")
        # Quem já colocou jogo em maratona de um servidor conta como "usa o bot" lá.
        self.conn.execute(
            "INSERT OR IGNORE INTO usuarios_servidores (user_id, guild_id)"
            " SELECT DISTINCT j.user_id, m.guild_id FROM jogos j JOIN maratonas m ON m.id = j.maratona_id"
        )

    def fechar(self) -> None:
        self.conn.close()

    # ---- maratonas -------------------------------------------------

    def criar_maratona(
        self, guild_id: int, channel_id: int, host_id: int, nome: str, limite: int | None = None
    ) -> Maratona:
        if self.maratona_aberta(guild_id):
            raise ErroMaratona("Já tem uma maratona aberta neste servidor. Encerre ela antes de abrir outra.")
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO maratonas (guild_id, channel_id, host_id, nome, limite, criada_em)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (guild_id, channel_id, host_id, nome, limite, agora()),
            )
        return self.maratona(cur.lastrowid)

    def maratona(self, maratona_id: int) -> Maratona | None:
        row = self.conn.execute("SELECT * FROM maratonas WHERE id = ?", (maratona_id,)).fetchone()
        return Maratona(**row) if row else None

    def maratona_aberta(self, guild_id: int) -> Maratona | None:
        row = self.conn.execute(
            "SELECT * FROM maratonas WHERE guild_id = ? AND status = 'aberta'", (guild_id,)
        ).fetchone()
        return Maratona(**row) if row else None

    def maratona_por_painel(self, message_id: int) -> Maratona | None:
        row = self.conn.execute(
            "SELECT * FROM maratonas WHERE panel_message_id = ?", (message_id,)
        ).fetchone()
        return Maratona(**row) if row else None

    def definir_painel(self, maratona_id: int, channel_id: int, message_id: int) -> None:
        with self.conn:
            self.conn.execute(
                "UPDATE maratonas SET channel_id = ?, panel_message_id = ? WHERE id = ?",
                (channel_id, message_id, maratona_id),
            )

    def encerrar(self, maratona_id: int) -> Maratona:
        """Fecha a maratona. Os jogos que não foram sorteados voltam para a lista de quem os colocou."""
        self._exigir_aberta(maratona_id)
        sobras: dict[int, list[str]] = {}
        for j in self.jogos(maratona_id):
            if not j.jogado:
                sobras.setdefault(j.user_id, []).append(j.nome)
        for user_id, nomes in sobras.items():
            self.salvar_na_lista(user_id, nomes)
        with self.conn:
            self.conn.execute(
                "UPDATE maratonas SET status = 'encerrada', encerrada_em = ? WHERE id = ?",
                (agora(), maratona_id),
            )
        return self.maratona(maratona_id)

    def _exigir_aberta(self, maratona_id: int) -> Maratona:
        m = self.maratona(maratona_id)
        if m is None or not m.aberta:
            raise ErroMaratona("Essa maratona já terminou.")
        return m

    # ---- jogos -----------------------------------------------------

    def adicionar_jogos(
        self, maratona_id: int, user_id: int, nomes: list[str]
    ) -> tuple[list[str], list[tuple[str, str]]]:
        """Coloca jogos na roleta.

        Devolve (adicionados, recusados), onde cada recusado vem com o motivo.
        """
        m = self._exigir_aberta(maratona_id)
        todos = self.jogos(maratona_id)
        ja_existe = {_chave(j.nome): j.jogado for j in todos}
        meus = sum(1 for j in todos if j.user_id == user_id)

        adicionados: list[str] = []
        recusados: list[tuple[str, str]] = []
        for nome in nomes:
            chave = _chave(nome)
            if chave in ja_existe:
                motivo = "já foi jogado nesta maratona" if ja_existe[chave] else "já está na roleta"
                recusados.append((nome, motivo))
            elif m.limite is not None and meus + len(adicionados) >= m.limite:
                recusados.append((nome, f"passou do limite de {m.limite} por pessoa"))
            else:
                adicionados.append(nome)
                ja_existe[chave] = False

        if adicionados:
            momento = agora()
            with self.conn:
                self.conn.executemany(
                    "INSERT INTO jogos (maratona_id, user_id, nome, adicionado_em) VALUES (?, ?, ?, ?)",
                    [(maratona_id, user_id, nome, momento) for nome in adicionados],
                )
        return adicionados, recusados

    def remover_jogo(self, maratona_id: int, user_id: int, jogo_id: int) -> Jogo:
        """Tira da roleta um jogo da própria pessoa que ainda não foi sorteado."""
        self._exigir_aberta(maratona_id)
        row = self.conn.execute(
            "SELECT * FROM jogos WHERE id = ? AND maratona_id = ? AND user_id = ?",
            (jogo_id, maratona_id, user_id),
        ).fetchone()
        if row is None:
            raise ErroMaratona("Não achei esse jogo entre os seus.")
        jogo = Jogo(**row)
        if jogo.jogado:
            raise ErroMaratona("Esse jogo já foi sorteado. Caiu, jogou! 😅")
        with self.conn:
            self.conn.execute("DELETE FROM jogos WHERE id = ?", (jogo_id,))
        return jogo

    def jogos(self, maratona_id: int) -> list[Jogo]:
        rows = self.conn.execute(
            "SELECT * FROM jogos WHERE maratona_id = ? ORDER BY id", (maratona_id,)
        ).fetchall()
        return [Jogo(**r) for r in rows]

    def jogos_do_usuario(self, maratona_id: int, user_id: int) -> list[Jogo]:
        return [j for j in self.jogos(maratona_id) if j.user_id == user_id]

    def jogadores(self, maratona_id: int) -> list[int]:
        """Todo mundo que está jogando: quem colocou jogo + quem entrou sem jogo."""
        rows = self.conn.execute(
            "SELECT user_id FROM (SELECT user_id, MIN(id) AS o FROM jogos WHERE maratona_id = ? GROUP BY user_id"
            " UNION ALL SELECT user_id, 1e18 AS o FROM maratona_extras WHERE maratona_id = ?)"
            " GROUP BY user_id ORDER BY MIN(o)",
            (maratona_id, maratona_id),
        ).fetchall()
        return [r[0] for r in rows]

    def extras(self, maratona_id: int) -> list[int]:
        """Quem entrou sem colocar jogo (e continua sem jogo)."""
        rows = self.conn.execute(
            "SELECT e.user_id FROM maratona_extras e WHERE e.maratona_id = ? AND NOT EXISTS"
            " (SELECT 1 FROM jogos j WHERE j.maratona_id = e.maratona_id AND j.user_id = e.user_id)",
            (maratona_id,),
        ).fetchall()
        return [r[0] for r in rows]

    def entrar_sem_jogo(self, maratona_id: int, user_id: int) -> bool:
        """True se entrou agora; False se já estava jogando."""
        self._exigir_aberta(maratona_id)
        if user_id in self.jogadores(maratona_id):
            return False
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO maratona_extras (maratona_id, user_id) VALUES (?, ?)",
                              (maratona_id, user_id))
        return True

    def sair_sem_jogo(self, maratona_id: int, user_id: int) -> None:
        self._exigir_aberta(maratona_id)
        if self.jogos_do_usuario(maratona_id, user_id):
            raise ErroMaratona("Essa pessoa colocou jogos nesta maratona, então continua contando como jogador.")
        with self.conn:
            cur = self.conn.execute("DELETE FROM maratona_extras WHERE maratona_id = ? AND user_id = ?",
                                    (maratona_id, user_id))
        if cur.rowcount == 0:
            raise ErroMaratona("Essa pessoa não estava na lista de quem entrou sem jogo.")

    def participantes(self, maratona_id: int) -> list[Participante]:
        """Quem colocou pelo menos um jogo, na ordem em que entrou."""
        rows = self.conn.execute(
            "SELECT user_id, COUNT(*) AS total, COUNT(ordem) AS jogados"
            " FROM jogos WHERE maratona_id = ? GROUP BY user_id ORDER BY MIN(id)",
            (maratona_id,),
        ).fetchall()
        return [Participante(**r) for r in rows]

    def contagem(self, maratona_id: int) -> tuple[int, int]:
        """(jogos ainda na roleta, jogos já jogados)."""
        row = self.conn.execute(
            "SELECT COUNT(*) - COUNT(ordem) AS restantes, COUNT(ordem) AS jogados"
            " FROM jogos WHERE maratona_id = ?",
            (maratona_id,),
        ).fetchone()
        return row["restantes"], row["jogados"]

    def ultimo_sorteado(self, maratona_id: int) -> Jogo | None:
        row = self.conn.execute(
            "SELECT * FROM jogos WHERE maratona_id = ? AND ordem IS NOT NULL ORDER BY ordem DESC LIMIT 1",
            (maratona_id,),
        ).fetchone()
        return Jogo(**row) if row else None

    def sortear(self, maratona_id: int) -> Sorteio | None:
        """Escolhe ao acaso um jogo que ainda está na roleta. None se a roleta estiver vazia.

        O resultado é gravado ANTES de qualquer animação no Discord, então um
        sorteio nunca se perde (nem se repete) se o bot cair no meio.
        """
        self._exigir_aberta(maratona_id)
        pendentes = self.conn.execute(
            "SELECT * FROM jogos WHERE maratona_id = ? AND ordem IS NULL", (maratona_id,)
        ).fetchall()
        if not pendentes:
            return None
        escolhido = Jogo(**_rng.choice(pendentes))
        with self.conn:
            numero = self.conn.execute(
                "SELECT COALESCE(MAX(ordem), 0) + 1 FROM jogos WHERE maratona_id = ?", (maratona_id,)
            ).fetchone()[0]
            momento = agora()
            self.conn.execute(
                "UPDATE jogos SET ordem = ?, sorteado_em = ? WHERE id = ?", (numero, momento, escolhido.id)
            )
            # Já foi jogado: sai da lista pessoal de quem colocou.
            self.conn.execute(
                "DELETE FROM lista_pessoal WHERE user_id = ? AND chave = ?",
                (escolhido.user_id, _chave(escolhido.nome)),
            )
        jogo = Jogo(**{**escolhido.__dict__, "ordem": numero, "sorteado_em": momento})
        return Sorteio(jogo=jogo, numero=numero, restantes=len(pendentes) - 1)

    # ---- lista pessoal ---------------------------------------------

    def salvar_na_lista(self, user_id: int, nomes: list[str]) -> tuple[list[str], list[tuple[str, str]]]:
        """Guarda jogos na lista pessoal. Devolve (salvos, recusados com motivo)."""
        chaves = {i.chave for i in self.lista(user_id)}
        salvos: list[str] = []
        recusados: list[tuple[str, str]] = []
        for nome in nomes:
            chave = _chave(nome)
            if chave in chaves:
                recusados.append((nome, "já está na sua lista"))
            elif len(chaves) >= MAX_LISTA:
                recusados.append((nome, f"sua lista chegou no máximo de {MAX_LISTA} jogos"))
            else:
                salvos.append(nome)
                chaves.add(chave)
        if salvos:
            momento = agora()
            with self.conn:
                self.conn.executemany(
                    "INSERT INTO lista_pessoal (user_id, nome, chave, salvo_em) VALUES (?, ?, ?, ?)",
                    [(user_id, nome, _chave(nome), momento) for nome in salvos],
                )
        return salvos, recusados

    def lista(self, user_id: int) -> list[ItemLista]:
        rows = self.conn.execute(
            "SELECT * FROM lista_pessoal WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
        return [ItemLista(**r) for r in rows]

    def lista_fora_da_maratona(self, user_id: int, maratona_id: int) -> list[ItemLista]:
        """Itens da lista que ainda não estão (nem estiveram) na maratona."""
        na_maratona = {_chave(j.nome) for j in self.jogos(maratona_id)}
        return [i for i in self.lista(user_id) if i.chave not in na_maratona]

    def remover_da_lista(self, user_id: int, item_id: int) -> ItemLista:
        row = self.conn.execute(
            "SELECT * FROM lista_pessoal WHERE id = ? AND user_id = ?", (item_id, user_id)
        ).fetchone()
        if row is None:
            raise ErroMaratona("Esse jogo não está mais na sua lista.")
        with self.conn:
            self.conn.execute("DELETE FROM lista_pessoal WHERE id = ?", (item_id,))
        return ItemLista(**row)

    # ---- mural da vergonha -----------------------------------------

    def registrar_reclamacao(self, maratona_id: int, user_id: int, denunciante_id: int) -> int:
        """Anota uma reclamação. Devolve o total de reclamações da pessoa nesta maratona."""
        self._exigir_aberta(maratona_id)
        ultima = self.conn.execute(
            "SELECT criada_em FROM reclamacoes WHERE maratona_id = ? AND user_id = ? AND denunciante_id = ?"
            " ORDER BY id DESC LIMIT 1",
            (maratona_id, user_id, denunciante_id),
        ).fetchone()
        if ultima is not None:
            passou = datetime.now(timezone.utc) - datetime.fromisoformat(ultima["criada_em"])
            if passou < timedelta(seconds=COOLDOWN_RECLAMACAO):
                raise ErroMaratona("Calma! Você acabou de dedurar essa pessoa. Espera uns segundinhos. 😄")

        atual = self.ultimo_sorteado(maratona_id)
        with self.conn:
            self.conn.execute(
                "INSERT INTO reclamacoes (maratona_id, user_id, denunciante_id, jogo_id, criada_em)"
                " VALUES (?, ?, ?, ?, ?)",
                (maratona_id, user_id, denunciante_id, atual.id if atual else None, agora()),
            )
        return self.conn.execute(
            "SELECT COUNT(*) FROM reclamacoes WHERE maratona_id = ? AND user_id = ?", (maratona_id, user_id)
        ).fetchone()[0]

    def mural(self, maratona_id: int) -> list[tuple[int, int]]:
        """Ranking de reclamações: [(user_id, quantidade), ...] do maior para o menor."""
        rows = self.conn.execute(
            "SELECT user_id, COUNT(*) AS n FROM reclamacoes WHERE maratona_id = ?"
            " GROUP BY user_id ORDER BY n DESC, MIN(id)",
            (maratona_id,),
        ).fetchall()
        return [(r["user_id"], r["n"]) for r in rows]

    # ---- radar de jogos --------------------------------------------

    def radar_adicionar_streamer(self, guild_id: int, login: str, twitch_id: str, nome: str) -> bool:
        """False se o servidor já acompanhava esse streamer."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO radar_streamers (guild_id, login, twitch_id, nome, adicionado_em)"
                " VALUES (?, ?, ?, ?, ?)",
                (guild_id, login.lower(), twitch_id, nome, agora()),
            )
        return cur.rowcount == 1

    def radar_remover_streamer(self, guild_id: int, login: str) -> bool:
        with self.conn:
            cur = self.conn.execute(
                "DELETE FROM radar_streamers WHERE guild_id = ? AND login = ?", (guild_id, login.lower())
            )
        return cur.rowcount == 1

    def radar_streamers(self, guild_id: int) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM radar_streamers WHERE guild_id = ? ORDER BY adicionado_em", (guild_id,)
        ).fetchall()

    def radar_todos_logins(self) -> list[str]:
        """Todos os streamers acompanhados por algum servidor (sem repetir)."""
        return [r[0] for r in self.conn.execute("SELECT DISTINCT login FROM radar_streamers ORDER BY login")]

    def radar_config(self, guild_id: int) -> tuple[int | None, int | None]:
        """(canal de alertas, preço máximo em centavos)."""
        row = self.conn.execute(
            "SELECT canal_alertas, preco_max FROM radar_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        return (row["canal_alertas"], row["preco_max"]) if row else (None, None)

    def radar_servidores_com_alertas(self) -> list[tuple[int, int]]:
        rows = self.conn.execute(
            "SELECT guild_id, canal_alertas FROM radar_config WHERE canal_alertas IS NOT NULL"
        ).fetchall()
        return [(r["guild_id"], r["canal_alertas"]) for r in rows]

    def radar_definir_canal(self, guild_id: int, canal_id: int | None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO radar_config (guild_id, canal_alertas) VALUES (?, ?)"
                " ON CONFLICT (guild_id) DO UPDATE SET canal_alertas = excluded.canal_alertas",
                (guild_id, canal_id),
            )

    def radar_definir_preco(self, guild_id: int, centavos: int | None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO radar_config (guild_id, preco_max) VALUES (?, ?)"
                " ON CONFLICT (guild_id) DO UPDATE SET preco_max = excluded.preco_max",
                (guild_id, centavos),
            )

    def radar_registrar_visto(self, login: str, game_id: str, game_name: str, quando_iso: str) -> bool:
        """Anota que o streamer jogou o jogo naquele dia. True se for a 1ª vez que ele aparece com esse jogo."""
        login = login.lower()
        primeira_vez = self.conn.execute(
            "SELECT 1 FROM radar_vistos WHERE login = ? AND game_id = ? LIMIT 1", (login, game_id)
        ).fetchone() is None
        with self.conn:
            self.conn.execute(
                "INSERT OR IGNORE INTO radar_vistos (login, game_id, dia, game_name) VALUES (?, ?, ?, ?)",
                (login, game_id, quando_iso[:10], game_name),
            )
        return primeira_vez

    def radar_vistos(self, guild_id: int, dias: int = JANELA_DIAS) -> list[tuple[str, str, str]]:
        """[(login, game_id, dia)] dos streamers deste servidor nos últimos `dias` dias."""
        desde = (datetime.now(timezone.utc) - timedelta(days=dias)).date().isoformat()
        rows = self.conn.execute(
            "SELECT v.login, v.game_id, v.dia FROM radar_vistos v"
            " JOIN radar_streamers s ON s.login = v.login AND s.guild_id = ?"
            " WHERE v.dia >= ?",
            (guild_id, desde),
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def radar_para_verificar(self, limite: int = 15) -> list[tuple[str, str]]:
        """Jogos vistos recentemente que ainda não foram (ou precisam ser de novo) procurados na Steam."""
        agora_dt = datetime.now(timezone.utc)
        desde = (agora_dt - timedelta(days=JANELA_DIAS)).date().isoformat()
        velho = (agora_dt - timedelta(days=RECHECAR_STEAM_DIAS)).isoformat(timespec="seconds")
        muito_velho = (agora_dt - timedelta(days=RECHECAR_SEM_STEAM_DIAS)).isoformat(timespec="seconds")
        rows = self.conn.execute(
            "SELECT v.game_id, MAX(v.game_name) FROM radar_vistos v"
            " LEFT JOIN radar_jogos j ON j.game_id = v.game_id"
            " WHERE v.dia >= ? AND (j.game_id IS NULL"
            "   OR (j.appid IS NOT NULL AND j.verificado_em < ?)"
            "   OR (j.appid IS NULL AND j.verificado_em < ?))"
            " GROUP BY v.game_id ORDER BY MAX(v.dia) DESC LIMIT ?",
            (desde, velho, muito_velho, limite),
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def radar_salvar_jogo(self, info: InfoJogo) -> None:
        dados = asdict(info)
        colunas = ", ".join(dados)
        marcas = ", ".join("?" for _ in dados)
        with self.conn:
            self.conn.execute(f"INSERT OR REPLACE INTO radar_jogos ({colunas}) VALUES ({marcas})",
                              tuple(dados.values()))

    def radar_jogos(self, game_ids: list[str] | None = None) -> dict[str, InfoJogo]:
        if game_ids is None:
            rows = self.conn.execute("SELECT * FROM radar_jogos").fetchall()
        else:
            marcas = ", ".join("?" for _ in game_ids) or "NULL"
            rows = self.conn.execute(f"SELECT * FROM radar_jogos WHERE game_id IN ({marcas})",
                                     tuple(game_ids)).fetchall()
        saida = {}
        for r in rows:
            d = dict(r)
            d["gratis"], d["multiplayer"], d["coop"] = bool(d["gratis"]), bool(d["multiplayer"]), bool(d["coop"])
            saida[d["game_id"]] = InfoJogo(**d)
        return saida

    def radar_marcar_avisado(self, guild_id: int, game_id: str) -> bool:
        """True se ainda não tinha avisado esse jogo nesse servidor."""
        with self.conn:
            cur = self.conn.execute(
                "INSERT OR IGNORE INTO radar_avisados (guild_id, game_id) VALUES (?, ?)", (guild_id, game_id)
            )
        return cur.rowcount == 1

    # ---- limpeza do chat -------------------------------------------

    def limpeza_config(self, guild_id: int) -> tuple[int | None, int]:
        """(canal do bot, minutos até apagar). Sem canal do bot configurado, nada é apagado."""
        row = self.conn.execute(
            "SELECT canal_bot, minutos FROM limpeza_config WHERE guild_id = ?", (guild_id,)
        ).fetchone()
        if row is None:
            return None, MINUTOS_LIMPEZA_PADRAO
        minutos = MINUTOS_LIMPEZA_PADRAO if row["minutos"] is None else row["minutos"]
        return row["canal_bot"], minutos

    def definir_canal_bot(self, guild_id: int, canal_id: int | None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO limpeza_config (guild_id, canal_bot) VALUES (?, ?)"
                " ON CONFLICT (guild_id) DO UPDATE SET canal_bot = excluded.canal_bot",
                (guild_id, canal_id),
            )
            if canal_id is None:  # sem canal do bot, a limpeza desliga: esquece o que estava agendado
                self.conn.execute("DELETE FROM limpeza_agenda WHERE guild_id = ?", (guild_id,))
            else:  # o que já estava no novo canal do bot não precisa mais sumir
                self.conn.execute("DELETE FROM limpeza_agenda WHERE channel_id = ?", (canal_id,))

    def definir_minutos_limpeza(self, guild_id: int, minutos: int) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO limpeza_config (guild_id, minutos) VALUES (?, ?)"
                " ON CONFLICT (guild_id) DO UPDATE SET minutos = excluded.minutos",
                (guild_id, minutos),
            )
            if minutos == 0:
                self.conn.execute("DELETE FROM limpeza_agenda WHERE guild_id = ?", (guild_id,))

    def agendar_apagar(self, message_id: int, channel_id: int, guild_id: int, apagar_em: datetime) -> None:
        """Agenda (ou reagenda) uma mensagem para ser apagada."""
        with self.conn:
            self.conn.execute(
                "INSERT INTO limpeza_agenda (message_id, channel_id, guild_id, apagar_em) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (message_id) DO UPDATE SET apagar_em = excluded.apagar_em",
                (message_id, channel_id, guild_id, apagar_em.isoformat(timespec="seconds")),
            )

    def adiar_apagar(self, message_id: int, apagar_em: datetime) -> bool:
        """Empurra o prazo de uma mensagem já agendada (alguém mexeu nela). False se não estava agendada."""
        with self.conn:
            cur = self.conn.execute(
                "UPDATE limpeza_agenda SET apagar_em = ? WHERE message_id = ?",
                (apagar_em.isoformat(timespec="seconds"), message_id),
            )
        return cur.rowcount == 1

    def mensagens_vencidas(self, agora_dt: datetime) -> list[tuple[int, int, int]]:
        """[(message_id, channel_id, guild_id)] cujo prazo já passou."""
        rows = self.conn.execute(
            "SELECT message_id, channel_id, guild_id FROM limpeza_agenda WHERE apagar_em <= ? ORDER BY apagar_em",
            (agora_dt.isoformat(timespec="seconds"),),
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]

    def desagendar(self, message_id: int) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM limpeza_agenda WHERE message_id = ?", (message_id,))

    # ---- promoções -------------------------------------------------

    def registrar_usuario_servidor(self, user_id: int, guild_id: int) -> None:
        with self.conn:
            self.conn.execute("INSERT OR IGNORE INTO usuarios_servidores (user_id, guild_id) VALUES (?, ?)",
                              (user_id, guild_id))

    def avisar_promocoes(self, guild_id: int) -> bool:
        row = self.conn.execute("SELECT avisar_promocoes FROM limpeza_config WHERE guild_id = ?",
                                (guild_id,)).fetchone()
        return True if row is None else bool(row[0])

    def definir_avisar_promocoes(self, guild_id: int, ativo: bool) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT INTO limpeza_config (guild_id, avisar_promocoes) VALUES (?, ?)"
                " ON CONFLICT (guild_id) DO UPDATE SET avisar_promocoes = excluded.avisar_promocoes",
                (guild_id, int(ativo)),
            )

    def servidores_para_promocoes(self) -> list[tuple[int, int]]:
        """[(guild_id, canal do bot)] dos servidores que querem avisos de promoção."""
        rows = self.conn.execute(
            "SELECT guild_id, canal_bot FROM limpeza_config WHERE canal_bot IS NOT NULL AND avisar_promocoes = 1"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def salvar_steam_nome(self, nome: str, appid: int | None) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO steam_nomes (chave, nome, appid, verificado_em) VALUES (?, ?, ?, ?)",
                (_chave(nome), nome, appid, agora()),
            )

    def nomes_sem_steam(self, limite: int = 30) -> list[str]:
        """Nomes das listas que ainda não sei qual jogo da Steam são."""
        velho = (datetime.now(timezone.utc) - timedelta(days=RECHECAR_NOME_STEAM_DIAS)).isoformat(timespec="seconds")
        rows = self.conn.execute(
            "SELECT MIN(l.nome) FROM lista_pessoal l LEFT JOIN steam_nomes s ON s.chave = l.chave"
            " WHERE s.chave IS NULL OR (s.appid IS NULL AND s.verificado_em < ?)"
            " GROUP BY l.chave LIMIT ?",
            (velho, limite),
        ).fetchall()
        return [r[0] for r in rows]

    def appids_das_listas(self) -> dict[int, str]:
        """{appid: nome} de todos os jogos que estão em alguma lista."""
        rows = self.conn.execute(
            "SELECT DISTINCT s.appid, s.nome FROM lista_pessoal l JOIN steam_nomes s ON s.chave = l.chave"
            " WHERE s.appid IS NOT NULL"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def atualizar_promocao(self, appid: int, nome: str, desconto: int,
                           preco_texto: str | None, original_texto: str | None) -> str | None:
        """Grava o desconto atual e diz se vale avisar.

        "nova" = acabou de entrar em promoção; "maior" = o desconto aumentou;
        None = nada a avisar (sem promoção, ou a mesma promoção de antes).
        """
        anterior = self.conn.execute("SELECT desconto FROM promocoes WHERE appid = ?", (appid,)).fetchone()
        with self.conn:
            if desconto <= 0:
                self.conn.execute("DELETE FROM promocoes WHERE appid = ?", (appid,))  # promoção acabou
                return None
            self.conn.execute(
                "INSERT INTO promocoes (appid, nome, desconto, preco_texto, original_texto, desde)"
                " VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (appid) DO UPDATE SET"
                " desconto = excluded.desconto, preco_texto = excluded.preco_texto,"
                " original_texto = excluded.original_texto",
                (appid, nome, desconto, preco_texto, original_texto, agora()),
            )
        if anterior is None:
            return "nova"
        return "maior" if desconto > anterior[0] else None

    def promocao(self, appid: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM promocoes WHERE appid = ?", (appid,)).fetchone()

    def status_steam_da_lista(self, user_id: int) -> dict[str, int | None]:
        """{chave: appid} dos jogos da lista já procurados na Steam (appid None = não achei)."""
        rows = self.conn.execute(
            "SELECT l.chave, s.appid FROM lista_pessoal l JOIN steam_nomes s ON s.chave = l.chave"
            " WHERE l.user_id = ?", (user_id,),
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def interessados(self, guild_id: int, appid: int) -> list[int]:
        """Quem usa o bot neste servidor e tem esse jogo na lista."""
        rows = self.conn.execute(
            "SELECT DISTINCT l.user_id FROM lista_pessoal l"
            " JOIN steam_nomes s ON s.chave = l.chave AND s.appid = ?"
            " JOIN usuarios_servidores u ON u.user_id = l.user_id AND u.guild_id = ?"
            " ORDER BY l.user_id",
            (appid, guild_id),
        ).fetchall()
        return [r[0] for r in rows]

    def promocoes_da_lista(self, user_id: int) -> dict[str, sqlite3.Row]:
        """{chave do jogo: promoção} dos jogos da lista da pessoa que estão em promoção agora."""
        rows = self.conn.execute(
            "SELECT l.chave, p.* FROM lista_pessoal l"
            " JOIN steam_nomes s ON s.chave = l.chave JOIN promocoes p ON p.appid = s.appid"
            " WHERE l.user_id = ?",
            (user_id,),
        ).fetchall()
        return {r["chave"]: r for r in rows}

    # ---- contas e bibliotecas da Steam -----------------------------

    def vincular_steam(self, user_id: int, steamid: str, nome: str | None, vinculado_por: int) -> None:
        with self.conn:
            antigo = self.conn.execute("SELECT steamid FROM steam_contas WHERE user_id = ?", (user_id,)).fetchone()
            if antigo and antigo[0] != steamid:  # trocou de conta: a biblioteca antiga não vale mais
                self.conn.execute("DELETE FROM steam_biblioteca WHERE user_id = ?", (user_id,))
            self.conn.execute(
                "INSERT INTO steam_contas (user_id, steamid, nome, vinculado_por) VALUES (?, ?, ?, ?)"
                " ON CONFLICT (user_id) DO UPDATE SET steamid = excluded.steamid, nome = excluded.nome,"
                " vinculado_por = excluded.vinculado_por, atualizado_em = NULL, publico = 0, total_jogos = 0",
                (user_id, steamid, nome, vinculado_por),
            )

    def desvincular_steam(self, user_id: int) -> bool:
        with self.conn:
            self.conn.execute("DELETE FROM steam_biblioteca WHERE user_id = ?", (user_id,))
            cur = self.conn.execute("DELETE FROM steam_contas WHERE user_id = ?", (user_id,))
        return cur.rowcount == 1

    def conta_steam(self, user_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM steam_contas WHERE user_id = ?", (user_id,)).fetchone()

    def contas_steam(self, user_ids: list[int]) -> dict[int, sqlite3.Row]:
        marcas = ", ".join("?" for _ in user_ids) or "NULL"
        rows = self.conn.execute(f"SELECT * FROM steam_contas WHERE user_id IN ({marcas})", tuple(user_ids))
        return {r["user_id"]: r for r in rows}

    def salvar_biblioteca(self, user_id: int, publico: bool, appids: list[int]) -> None:
        with self.conn:
            self.conn.execute("DELETE FROM steam_biblioteca WHERE user_id = ?", (user_id,))
            self.conn.executemany("INSERT OR IGNORE INTO steam_biblioteca (user_id, appid) VALUES (?, ?)",
                                  [(user_id, a) for a in appids])
            self.conn.execute(
                "UPDATE steam_contas SET publico = ?, total_jogos = ?, atualizado_em = ? WHERE user_id = ?",
                (int(publico), len(appids), agora(), user_id),
            )

    def bibliotecas(self, user_ids: list[int]) -> dict[int, set[int]]:
        """{user_id: {appids}} só de quem tem conta vinculada com jogos visíveis."""
        saida: dict[int, set[int]] = {}
        for uid, conta in self.contas_steam(user_ids).items():
            if conta["publico"]:
                saida[uid] = {r[0] for r in self.conn.execute(
                    "SELECT appid FROM steam_biblioteca WHERE user_id = ?", (uid,))}
        return saida

    def salvar_app(self, app) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO steam_apps (appid, nome, gratis, preco_centavos, preco_texto, desconto,"
                " multiplayer, coop, verificado_em) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (app.appid, app.nome, int(app.gratis), app.preco_centavos, app.preco_texto, app.desconto,
                 int(app.multiplayer), int(app.coop), app.verificado_em),
            )

    def apps(self, appids: list[int]) -> dict[int, sqlite3.Row]:
        marcas = ", ".join("?" for _ in appids) or "NULL"
        rows = self.conn.execute(f"SELECT * FROM steam_apps WHERE appid IN ({marcas})", tuple(appids))
        return {r["appid"]: r for r in rows}

    def appids_das_listas_de(self, user_ids: list[int]) -> set[int]:
        """Jogos (já identificados na Steam) que estão nas listas pessoais dessas pessoas."""
        marcas = ", ".join("?" for _ in user_ids) or "NULL"
        rows = self.conn.execute(
            f"SELECT DISTINCT s.appid FROM lista_pessoal l JOIN steam_nomes s ON s.chave = l.chave"
            f" WHERE s.appid IS NOT NULL AND l.user_id IN ({marcas})", tuple(user_ids))
        return {r[0] for r in rows}
