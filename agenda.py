"""Datas e horários digitados para agendar uma maratona ("sábado" + "21h30").

Módulo puro (sem Discord nem banco), testado em tests/test_agenda.py.
Tudo no horário de Brasília (sem horário de verão desde 2019).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import date, datetime, time, timedelta, timezone

BRASILIA = timezone(timedelta(hours=-3))
LEMBRETE_MINUTOS = 15         # quanto tempo antes o bot abre a maratona e chama o pessoal
MAX_DIAS_FRENTE = 180         # não dá para marcar com mais de ~6 meses de antecedência
TOLERANCIA_ATRASO = timedelta(hours=2)  # bot ficou desligado e voltou depois do horário: até 2h ainda abre

DIAS_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")
_DIAS = {
    "segunda": 0, "seg": 0, "terca": 1, "ter": 1, "quarta": 2, "qua": 2, "quinta": 3, "qui": 3,
    "sexta": 4, "sex": 4, "sabado": 5, "sab": 5, "domingo": 6, "dom": 6,
}


class ErroData(ValueError):
    """Data ou hora que não deu para entender. A mensagem já vem pronta para mostrar."""


def _limpar(texto: str) -> str:
    """Minúsculas, sem acentos e sem espaços sobrando ("Sábado " → "sabado")."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return " ".join(sem_acento.lower().split())


def ler_hora(texto: str) -> time:
    """"21", "21h", "21:30", "21h30", "9h05" → horário."""
    m = re.fullmatch(r"(\d{1,2})\s*(?:[:h]\s*(\d{2})?)?\s*(?:h|hs|horas?)?", _limpar(texto))
    if not m:
        raise ErroData(f"Não entendi o horário **{texto}**. Escreva assim: `21h`, `21:30` ou `21h30`.")
    hora, minuto = int(m[1]), int(m[2] or 0)
    if hora > 23 or minuto > 59:
        raise ErroData(f"**{texto}** não é um horário que existe.")
    return time(hora, minuto)


def ler_dia(texto: str, hoje: date) -> date:
    """"hoje", "amanhã", "sábado", "sexta-feira", "25/10", "25/10/2026" → data.

    Dia da semana = o próximo que vier (hoje conta, se for o mesmo dia da semana).
    Dia/mês sem ano = este ano; se já passou, o ano que vem.
    """
    t = _limpar(texto)
    if t == "hoje":
        return hoje
    if t in ("amanha", "amanhã"):
        return hoje + timedelta(days=1)
    nome = t.removesuffix("-feira").removesuffix(" feira").split(" (")[0]
    if nome in _DIAS:
        return hoje + timedelta(days=(_DIAS[nome] - hoje.weekday()) % 7)
    m = re.fullmatch(r"(\d{1,2})[/.-](\d{1,2})(?:[/.-](\d{2}|\d{4}))?", t.split(" ")[0])
    if not m:
        raise ErroData(f"Não entendi o dia **{texto}**. Escreva `hoje`, `amanhã`, um dia da semana "
                       "(`sábado`) ou a data (`25/10`).")
    dia, mes = int(m[1]), int(m[2])
    ano = int(m[3]) if m[3] else hoje.year
    if ano < 100:
        ano += 2000
    try:
        d = date(ano, mes, dia)
    except ValueError:
        raise ErroData(f"**{texto}** não é uma data que existe.") from None
    if not m[3] and d < hoje:
        d = d.replace(year=ano + 1)
    return d


def quando(dia: str, hora: str, agora: datetime) -> datetime:
    """Junta dia + hora digitados num momento (com fuso de Brasília) e confere se está no futuro.

    Se a pessoa escreveu um dia da semana e o horário de hoje já passou, vale o da semana que vem
    ("sábado 21h" escrito num sábado às 23h = o próximo sábado).
    """
    agora_br = agora.astimezone(BRASILIA)
    h = ler_hora(hora)
    d = ler_dia(dia, agora_br.date())
    momento = datetime.combine(d, h, tzinfo=BRASILIA)
    eh_dia_da_semana = _limpar(dia).removesuffix("-feira").removesuffix(" feira").split(" (")[0] in _DIAS
    if momento <= agora_br and eh_dia_da_semana:
        momento += timedelta(days=7)
    if momento <= agora_br:
        raise ErroData("Esse horário já passou! Escolha um momento no futuro.")
    if momento - agora_br > timedelta(days=MAX_DIAS_FRENTE):
        raise ErroData("Dá para marcar com no máximo 6 meses de antecedência.")
    return momento


def sugestoes_de_dia(agora: datetime, digitado: str = "", dias: int = 14) -> list[tuple[str, str]]:
    """Opções para o autocompletar do dia: [(texto mostrado, valor)], ex.: ("sábado, 25/10", "25/10/2026")."""
    hoje = agora.astimezone(BRASILIA).date()
    filtro = _limpar(digitado)
    opcoes = []
    for i in range(dias):
        d = hoje + timedelta(days=i)
        rotulo = {0: "hoje", 1: "amanhã"}.get(i, DIAS_SEMANA[d.weekday()])
        texto = f"{rotulo}, {DIAS_SEMANA[d.weekday()]} {d:%d/%m}" if i < 2 else f"{rotulo}, {d:%d/%m}"
        if not filtro or filtro in _limpar(texto):
            opcoes.append((texto, d.strftime("%d/%m/%Y")))
    return opcoes


def nome_do_dia(momento: datetime) -> str:
    """"sábado, 25/10 às 21h30" (horário de Brasília)."""
    m = momento.astimezone(BRASILIA)
    hora = f"{m:%H}h" + (f"{m:%M}" if m.minute else "")
    return f"{DIAS_SEMANA[m.weekday()]}, {m:%d/%m} às {hora}"
