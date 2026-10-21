"""Imagens geradas pelo bot (Manual da Marca, pág. 7): card da roleta e card do resumo.

Medidas do manual: 800 × 420 px, desenhado em 2x (1600 × 840) para ficar nítido,
margem interna de 44 px, linhas de CRT de 2 px a cada 4 px em branco 3,5%,
títulos em Silkscreen (maiúsculas, sombra magenta) e detalhes em JetBrains Mono Bold.

As funções devolvem o PNG pronto (BytesIO) para anexar com discord.File.
Não acessam o Discord nem a internet, então dá para gerar e conferir as imagens sozinhas.
"""

from __future__ import annotations

from functools import lru_cache
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

from marca import AMARELO, CIANO, FUNDO_CRT, MAGENTA, PASTA_FONTES, PASTA_NANDO, TINTA, VERDE, VERMELHO

LARGURA, ALTURA = 800, 420
ESCALA = 2           # desenha em 2x
MARGEM = 44
SOMBRA = 5           # deslocamento da sombra magenta do título (manual: 4 a 7 px)

PIXEL = "Silkscreen-Regular.ttf"
MONO = "JetBrainsMono-Bold.ttf"


# ---------------------------------------------------------------- utilidades

@lru_cache(maxsize=64)
def _fonte(arquivo: str, tamanho: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(PASTA_FONTES / arquivo), tamanho * ESCALA)


@lru_cache(maxsize=8)
def _nando(arquivo: str) -> Image.Image:
    return Image.open(PASTA_NANDO / arquivo).convert("RGBA")


def _s(v: float) -> int:
    return round(v * ESCALA)


def _largura(texto: str, arquivo: str, tamanho: int) -> float:
    return _fonte(arquivo, tamanho).getlength(texto) / ESCALA


def _escrever(d: ImageDraw.ImageDraw, x: float, y: float, texto: str, arquivo: str, tamanho: int,
              cor, sombra: bool = False, direita: bool = False) -> None:
    fonte = _fonte(arquivo, tamanho)
    if direita:
        x -= _largura(texto, arquivo, tamanho)
    if sombra:
        d.text((_s(x + SOMBRA), _s(y + SOMBRA)), texto, font=fonte, fill=MAGENTA)
    d.text((_s(x), _s(y)), texto, font=fonte, fill=cor)


def _cortar(texto: str, arquivo: str, tamanho: int, largura: float) -> str:
    """Corta o texto com "..." para caber na largura."""
    if _largura(texto, arquivo, tamanho) <= largura:
        return texto
    while texto and _largura(texto + "...", arquivo, tamanho) > largura:
        texto = texto[:-1]
    return texto.rstrip() + "..."


def _quebrar(texto: str, arquivo: str, largura: float, maior: int, menor: int,
             max_linhas: int = 2) -> tuple[int, list[str]]:
    """Escolhe o maior tamanho de fonte em que o texto cabe em até max_linhas linhas."""
    palavras = texto.split()
    for tamanho in range(maior, menor - 1, -2):
        linhas: list[str] = []
        atual = ""
        for p in palavras:
            teste = f"{atual} {p}".strip()
            if _largura(teste, arquivo, tamanho) <= largura or not atual:
                atual = teste
            else:
                linhas.append(atual)
                atual = p
        linhas.append(atual)
        if len(linhas) <= max_linhas and all(_largura(l, arquivo, tamanho) <= largura for l in linhas):
            return tamanho, linhas
    # nem no menor tamanho coube: usa o menor e corta a última linha
    linhas = linhas[:max_linhas]
    linhas[-1] = _cortar(" ".join([linhas[-1]] + palavras[len(" ".join(linhas).split()):]), arquivo, menor, largura)
    return menor, linhas


def _base(cor_moldura) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    """Fundo CRT com as linhas de tela e a moldura colorida de 6 px."""
    img = Image.new("RGBA", (_s(LARGURA), _s(ALTURA)), FUNDO_CRT + (255,))
    linhas = Image.new("RGBA", img.size, (0, 0, 0, 0))
    dl = ImageDraw.Draw(linhas)
    for y in range(0, _s(ALTURA), _s(4)):
        dl.rectangle((0, y, img.width, y + _s(2) - 1), fill=(255, 255, 255, round(255 * 0.035)))
    img.alpha_composite(linhas)
    d = ImageDraw.Draw(img)
    d.rectangle((0, 0, img.width - 1, img.height - 1), outline=cor_moldura, width=_s(6))
    return img, d


def _colar_nando(img: Image.Image, arquivo: str, altura: float, x_direita: float, y_base: float) -> tuple[float, float]:
    """Cola o Nando com a altura pedida, alinhado à direita e embaixo. Devolve (x, y) do canto de cima."""
    n = _nando(arquivo)
    h = _s(altura)
    w = round(n.width * h / n.height)
    x, y = _s(x_direita) - w, _s(y_base) - h
    img.alpha_composite(n.resize((w, h), Image.LANCZOS), (x, y))
    return x / ESCALA, y / ESCALA


def _png(img: Image.Image) -> BytesIO:
    saida = BytesIO()
    img.convert("RGB").save(saida, "PNG", optimize=True)
    saida.seek(0)
    return saida


# ---------------------------------------------------------------- card da roleta

def card_roleta(numero: int, jogo: str, escolhido_por: str, rodape: str = "", restantes: int = 0) -> BytesIO:
    """Card da revelação do sorteio.

    rodape: a linha de quem tem o jogo (ex.: "3 TÊM · FALTA PRA DUDA: R$ 14,87"), vazia se não tiver.
    """
    img, d = _base(CIANO)
    x = MARGEM
    area_texto = 400  # o resto é do Nando e dos traços de velocidade

    # Nando surpreso à direita, com os traços de velocidade
    nx, ny = _colar_nando(img, "nando-surpreso.png", 186, LARGURA - MARGEM - 6, 262)
    meio = ny + 95
    for i, (comp, cor) in enumerate(((42, CIANO), (26, MAGENTA), (36, CIANO))):
        y = meio - 26 + i * 20
        d.rectangle((_s(nx - 18 - comp), _s(y), _s(nx - 18), _s(y + 6)), fill=cor)

    _escrever(d, x, MARGEM, f"JOGO #{numero} DA NOITE", PIXEL, 16, CIANO)

    tamanho, linhas = _quebrar(jogo.upper(), PIXEL, area_texto, 48, 22)
    y = MARGEM + 38
    for linha in linhas:
        _escrever(d, x, y, linha, PIXEL, tamanho, AMARELO, sombra=True)
        y += tamanho * 1.15
    _escrever(d, x, y + 12, _cortar(f"ESCOLHIDO POR {escolhido_por.upper()}", PIXEL, 16, area_texto),
              PIXEL, 16, TINTA)

    # regra de ouro numa caixa magenta
    regra = "CAIU, JOGOU. SEM RECLAMAR!"
    caixa_y = ALTURA - MARGEM - 26 - 44
    largura_regra = _largura(regra, PIXEL, 16)
    d.rectangle((_s(x), _s(caixa_y), _s(x + largura_regra + 28), _s(caixa_y + 38)), outline=MAGENTA, width=_s(2))
    _escrever(d, x + 14, caixa_y + 9, regra, PIXEL, 16, MAGENTA)

    # rodapé
    base = ALTURA - MARGEM - 14
    texto_restam = "A ROLETA ESTÁ VAZIA" if restantes == 0 else f"AINDA {restantes} NA ROLETA"
    _escrever(d, LARGURA - MARGEM, base, texto_restam, MONO, 13, TINTA, direita=True)
    if rodape:
        espaco = LARGURA - 2 * MARGEM - _largura(texto_restam, MONO, 13) - 24
        _escrever(d, x, base, _cortar(rodape.upper(), MONO, 13, espaco), MONO, 13, CIANO)
    return _png(img)


# ---------------------------------------------------------------- card do resumo

def card_resumo(nome: str, jogos: list[tuple[str, str]], cabecalho: str,
                mural: list[tuple[str, int]]) -> BytesIO:
    """Card do resumo final, estilo tabela de recordes.

    jogos: [(nome do jogo, quem escolheu)] na ordem em que foram jogados.
    cabecalho: o canto de cima à direita (ex.: "4 JOGOS · 3H42").
    mural: [(nome, quantas reclamações)] do maior para o menor.
    """
    img, d = _base(VERDE)
    x = MARGEM
    direita = LARGURA - MARGEM

    _escrever(d, x, MARGEM, "FIM DA MARATONA", PIXEL, 16, VERDE)
    _escrever(d, direita, MARGEM + 22, cabecalho.upper(), MONO, 14, TINTA, direita=True)
    largura_titulo = LARGURA - 2 * MARGEM - _largura(cabecalho.upper(), MONO, 14) - 30
    tamanho, linhas = _quebrar(nome.upper(), PIXEL, largura_titulo, 32, 18, max_linhas=1)
    _escrever(d, x, MARGEM + 22, linhas[0], PIXEL, tamanho, AMARELO, sombra=True)

    # coluna da esquerda: o que jogamos
    col_fim = 520
    topo = MARGEM + 92
    passo = 31
    _escrever(d, x, topo, "HIGH SCORES · O QUE JOGAMOS", MONO, 12, CIANO)
    mostrar = jogos[:6] if len(jogos) <= 6 else jogos[:5]
    y = topo + 26
    for i, (jogo, quem) in enumerate(mostrar, start=1):
        quem = _cortar(quem.upper(), MONO, 13, 110)
        _escrever(d, x, y + 1, f"{i}.", MONO, 13, AMARELO)
        _escrever(d, col_fim, y + 1, quem, MONO, 13, CIANO, direita=True)
        espaco = col_fim - (x + 34) - _largura(quem, MONO, 13) - 14
        _escrever(d, x + 34, y, _cortar(jogo.upper(), PIXEL, 16, espaco), PIXEL, 16, TINTA)
        y += passo
    if len(jogos) > 6:
        _escrever(d, x + 34, y + 1, f"...E MAIS {len(jogos) - 5}", MONO, 13, CIANO)
    if not jogos:
        _escrever(d, x + 34, y, "NENHUM JOGO SORTEADO", PIXEL, 16, TINTA)

    # coluna da direita: mural da vergonha
    mx = col_fim + 40
    _escrever(d, mx, topo, "MURAL DA VERGONHA", MONO, 12, VERMELHO)
    y = topo + 26
    if mural:
        for quem, n in mural[:4]:
            _escrever(d, direita, y + 1, f"×{n}", MONO, 13, VERMELHO, direita=True)
            _escrever(d, mx, y, _cortar(quem.upper(), PIXEL, 16, direita - mx - 44), PIXEL, 16, TINTA)
            y += passo
    else:
        _escrever(d, mx, y, "NINGUÉM", PIXEL, 16, TINTA)
        _escrever(d, mx, y + passo, "RECLAMOU!", PIXEL, 16, TINTA)

    # Nando (regata verde) e a chamada final
    _colar_nando(img, "nando-sorriso-verde.png", 78, direita, ALTURA - MARGEM + 8)
    _escrever(d, x, ALTURA - MARGEM - 16, "GG A TODOS! CONTINUE? /MARATONA CRIAR", PIXEL, 16, VERDE)
    return _png(img)
