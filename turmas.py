"""Textos do "Quem topa?": a enquete que monta a turma de um jogo.

Como funciona:
  1. Alguém pergunta quem topa jogar um jogo (/topa ou o botão 🗳️ da ficha do /jogo).
  2. Cada um responde 🙋 Topo · 🤔 Talvez · 🙅 Passo. Quem topa entra na turma do jogo.
  3. A turma é chamada (marcada) quando alguém faz um /bora desse jogo, pelo botão 📣 da enquete
     (que abre o /bora) ou quando o jogo sai na roleta de uma maratona. Aí ela fecha: o jogo foi jogado.

Módulo puro (sem Discord), testado em tests/test_turmas.py.
"""

from __future__ import annotations

# resposta → (rótulo do botão, emoji, título da linha na enquete)
RESPOSTAS = {
    "topo": ("Topo", "🙋", "Topam"),
    "talvez": ("Talvez", "🤔", "Talvez"),
    "passo": ("Passo", "🙅", "Passam"),
}
MAX_TURMAS_NO_PAINEL = 3


def linhas_respostas(respostas: dict[str, list[int]]) -> list[str]:
    """Uma linha por resposta: "🙋 **Topam (2)**: <@1> <@2>"."""
    linhas = []
    for chave, (_, emoji, titulo) in RESPOSTAS.items():
        ids = respostas.get(chave, [])
        linhas.append(f"{emoji} **{titulo} ({len(ids)})**: " + (" ".join(f"<@{u}>" for u in ids) if ids else "—"))
    return linhas


def para_marcar(respostas: dict[str, list[int]], tirar: int | None = None) -> list[int]:
    """Quem a chamada marca: só quem topou (nunca quem disse talvez ou passo), menos quem está chamando."""
    return [u for u in respostas.get("topo", []) if u != tirar]


def texto_chamada(nome: str, ids: list[int]) -> str:
    mencoes = " ".join(f"<@{u}>" for u in ids)
    return f"🙋 {mencoes}: vocês toparam **{nome}** e agora vai rolar! Respondam no chamado acima. 🎮"


def resumo_painel(turmas: list[tuple[str, int]], limite: int = MAX_TURMAS_NO_PAINEL) -> str | None:
    """Linha do painel da maratona: "**PEAK** (5 🙋) · **REPO** (3 🙋)", ou None se não tem turma."""
    if not turmas:
        return None
    texto = " · ".join(f"**{nome}** ({n} 🙋)" for nome, n in turmas[:limite])
    if len(turmas) > limite:
        texto += f" · e mais {len(turmas) - limite}"
    return texto


def url_capa(appid: int | None) -> str | None:
    """Imagem de capa da loja da Steam (a mesma do topo da página do jogo)."""
    return f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg" if appid else None
