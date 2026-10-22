"""Testes do agendamento: datas digitadas e regras do banco."""

from datetime import datetime, timezone

import pytest

from agenda import ErroData, ler_dia, ler_hora, nome_do_dia, quando, sugestoes_de_dia
from db import Database, ErroMaratona

# quinta-feira, 22/10/2026, 00:40 em Brasília (03:40 UTC)
AGORA = datetime(2026, 10, 22, 3, 40, tzinfo=timezone.utc)


@pytest.mark.parametrize("dia, hora, esperado", [
    ("sábado", "21h30", "sábado, 24/10 às 21h30"),
    ("Sexta-feira", "21", "sexta, 23/10 às 21h"),
    ("sab", "21:00", "sábado, 24/10 às 21h"),
    ("hoje", "23:00", "quinta, 22/10 às 23h"),
    ("amanhã", "20h", "sexta, 23/10 às 20h"),
    ("amanha", "8h05", "sexta, 23/10 às 08h05"),
    ("25/10", "21:15", "domingo, 25/10 às 21h15"),
    ("01/01", "20", "sexta, 01/01 às 20h"),             # já passou este ano: o ano que vem
    ("quinta", "00:10", "quinta, 29/10 às 00h10"),      # hoje é quinta, mas 00:10 já passou: a próxima
    ("quinta", "23h", "quinta, 22/10 às 23h"),          # hoje mais tarde ainda vale
    ("24/10/2026", "21h", "sábado, 24/10 às 21h"),      # valor que o autocompletar manda
])
def test_datas_que_funcionam(dia, hora, esperado):
    assert nome_do_dia(quando(dia, hora, AGORA)) == esperado


def test_quando_devolve_horario_de_brasilia():
    momento = quando("sábado", "21h", AGORA)
    assert momento.astimezone(timezone.utc).hour == 0  # 21h em Brasília = meia-noite UTC


@pytest.mark.parametrize("dia, hora, pedaco", [
    ("hoje", "00:10", "já passou"),
    ("31/02", "20", "não é uma data"),
    ("xyz", "20", "Não entendi o dia"),
    ("sab", "25h", "não é um horário"),
    ("sab", "noite", "Não entendi o horário"),
    ("21/10/2026", "20", "já passou"),
    ("01/12/2027", "20", "6 meses"),
])
def test_datas_que_nao_funcionam(dia, hora, pedaco):
    with pytest.raises(ErroData, match=pedaco):
        quando(dia, hora, AGORA)


def test_pedacos():
    assert ler_hora("21h30").minute == 30 and ler_hora(" 9 h ").hour == 9
    assert ler_dia("domingo", AGORA.date()).isoformat() == "2026-10-25"


def test_sugestoes_do_autocompletar():
    s = sugestoes_de_dia(AGORA)
    assert s[0] == ("hoje, quinta 22/10", "22/10/2026")
    assert s[1] == ("amanhã, sexta 23/10", "23/10/2026")
    assert sugestoes_de_dia(AGORA, "sab") == [("sábado, 24/10", "24/10/2026"), ("sábado, 31/10", "31/10/2026")]
    assert quando(s[2][1], "21h", AGORA)  # o valor sugerido é aceito de volta


# ---------------------------------------------------------------- banco

def test_agendamento_no_banco():
    db = Database(":memory:")
    ag = db.criar_agendamento(1, 10, 100, "Sábado", "2026-10-25T00:00:00+00:00")
    db.responder_agendamento(ag.id, 100, "vou")
    db.responder_agendamento(ag.id, 200, "talvez")
    db.responder_agendamento(ag.id, 200, "vou")       # mudou de ideia
    db.responder_agendamento(ag.id, 300, "nao")
    assert db.respostas_agendamento(ag.id) == {"vou": [100, 200], "talvez": [], "nao": [300]}

    db.definir_mensagem_agendamento(ag.id, 555, 777)
    assert db.agendamento_por_mensagem(555).evento_id == 777

    assert db.agendamentos_vencendo("2026-10-24T23:44:00+00:00") == []
    assert [a.id for a in db.agendamentos_vencendo("2026-10-24T23:45:00+00:00")] == []  # ainda falta
    assert [a.id for a in db.agendamentos_vencendo("2026-10-25T00:00:00+00:00")] == [ag.id]

    assert db.mudar_status_agendamento(ag.id, "aberto") is True
    assert db.mudar_status_agendamento(ag.id, "aberto") is False    # não abre duas vezes
    assert db.agendamentos_vencendo("2027-01-01T00:00:00+00:00") == []
    with pytest.raises(ErroMaratona, match="não está mais valendo"):
        db.responder_agendamento(ag.id, 400, "vou")
    db.fechar()
