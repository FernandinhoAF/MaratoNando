"""Testes do /sugestao: leitura do HTML da busca da Steam e filtro de friendslop.

O HTML abaixo segue a estrutura real da busca (store.steampowered.com/search/results/?infinite=1),
conferida em out/2026, com as tags e preços reais desses jogos.
"""

from sugestao import eh_friendslop_loja, filtrar, ler_resultados


def linha(appid, titulo, tags, preco, texto_preco, pct, total, desconto=0):
    bloco_desconto = (f'<div class="discount_block search_discount_block" data-price-final="{preco}" '
                      f'data-bundlediscount="0" data-discount="{desconto}"><div class="discount_pct">-{desconto}%</div>'
                      if desconto else '<div class="discount_block no_discount search_discount_block">')
    return (
        f'<a href="https://store.steampowered.com/app/{appid}/X/?snr=1_7_7_230_150_1" data-ds-appid="{appid}" '
        f'data-ds-itemkey="App_{appid}" data-ds-tagids="[{",".join(map(str, tags))}]" data-ds-descids="[]" '
        f'onmouseover="GameHover( this, event, \'global_hover\', {{&quot;type&quot;:&quot;app&quot;}} );" '
        f'class="search_result_row ds_collapse_flag " data-search-page="1" data-gpnav="item">'
        f'<div class="col search_capsule"><img src="https://shared.fastly.steamstatic.com/apps/{appid}/capsule_sm_120.jpg?t=1"></div>'
        f'<div class="responsive_search_name_combined"><div class="col search_name ellipsis">'
        f'<span class="title">{titulo}</span></div>'
        f'<div class="col search_released responsive_secondrow">16 Jun, 2025</div>'
        f'<div class="col search_reviewscore responsive_secondrow">'
        f'<span class="search_review_summary positive" data-tooltip-html="Very Positive&lt;br&gt;{pct}% of the '
        f'{total} user reviews for this game are positive.&lt;br&gt;&lt;br&gt;"></span></div>'
        f'<div class="col search_price_discount_combined responsive_secondrow" data-price-final="{preco}">'
        f'<div class="search_discount_and_price responsive_secondrow">{bloco_desconto}'
        f'<div class="discount_prices"><div class="discount_final_price">{texto_preco}</div></div></div></div></div>'
        f'</div></a>'
    )


HTML = "".join([
    # friendslop de verdade (tags reais do BOMBANANA!: Multiplayer, Online Co-Op, ..., Party Game, Comedy, Casual)
    linha(4656000, "BOMBANANA!", [3859, 3843, 5711, 1664, 7178, 1719, 597], 1199, "R$11,99", 92, "5,432"),
    # co-op mas sem tag de diversão (Risk of Rain 2)
    linha(632360, "Risk of Rain 2", [3814, 42804, 3859, 1685, 1716, 3959, 19], 1979, "R$19,79", 92, "250,000"),
    # MMO grátis (AION 2)
    linha(3393110, "AION 2", [113, 1754, 128, 21, 1695, 4747, 7926], 0, "Free", 57, "10,889"),
    # divertido e co-op, mas caro
    linha(1, "Jogo Caro e Engraçado", [3843, 4136], 12000, "R$120,00", 95, "9,000"),
    # divertido e co-op, mas mal avaliado
    linha(2, "Jogo Engraçado Ruim", [3843, 4136], 1000, "R$10,00", 61, "9,000"),
    # divertido, co-op, em promoção, com nome com &amp;
    linha(3, "Pizza &amp; Caos", [3843, 4136, 3968], 950, "R$9,50", 88, "1,204", desconto=50),
    # repetido (aparece de novo em outra busca)
    linha(4656000, "BOMBANANA!", [3859, 3843, 5711, 1664, 7178, 1719, 597], 1199, "R$11,99", 92, "5,432"),
    # linha de pacote, sem appid único: deve ser ignorada
    '<a href="x" data-ds-packageid="99" class="search_result_row"><span class="title">Pacote</span></a>',
])


def test_le_os_campos_da_busca():
    itens = ler_resultados(HTML)
    assert len(itens) == 7  # o pacote foi ignorado
    b = itens[0]
    assert (b.appid, b.nome, b.preco_centavos, b.preco_texto, b.desconto) == (4656000, "BOMBANANA!", 1199, "R$11,99", 0)
    assert (b.review_pct, b.review_total) == (0.92, 5432)
    assert b.tags_divertidas == ["Party Game", "Comedy", "Casual"]
    assert b.url == "https://store.steampowered.com/app/4656000/"
    pizza = itens[5]
    assert pizza.nome == "Pizza & Caos" and pizza.desconto == 50 and pizza.preco_centavos == 950
    assert itens[2].gratis


def test_filtro_deixa_so_friendslop_sem_repetir():
    nomes = [i.nome for i in filtrar(ler_resultados(HTML))]
    assert nomes == ["BOMBANANA!", "Pizza & Caos"]


def test_preco_maximo_do_servidor_vale_aqui_tambem():
    itens = ler_resultados(HTML)
    assert [i.nome for i in filtrar(itens, preco_max=1000)] == ["Pizza & Caos"]
    assert "Jogo Caro e Engraçado" in [i.nome for i in filtrar(itens, preco_max=15000)]


def test_poucas_avaliacoes_nao_passam():
    item = ler_resultados(linha(5, "Novinho", [3843, 4136], 1000, "R$10,00", 100, "37"))[0]
    assert not eh_friendslop_loja(item)


def test_html_vazio_ou_estranho_nao_quebra():
    assert ler_resultados("") == []
    assert ler_resultados("<div>nada aqui</div>") == []
