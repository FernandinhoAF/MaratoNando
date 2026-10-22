"""Monta o .zip para subir o bot na Discloud.

Uso (na pasta do bot, com o PowerShell):

    python empacotar_discloud.py              # para ATUALIZAR o bot (não leva o banco de dados)
    python empacotar_discloud.py --com-banco  # para a PRIMEIRA subida (leva o banco, com tudo que já foi configurado)

O zip só leva o que o bot precisa para rodar. Ficam de fora: .venv, .git, testes,
__pycache__ e, por padrão, o banco de dados. O banco fica de fora nas atualizações
para não apagar o banco que está rodando na Discloud (com as maratonas, listas,
contas da Steam etc. de lá) substituindo por uma cópia velha do seu PC.
"""

import sys
import zipfile
from pathlib import Path

PASTA = Path(__file__).resolve().parent
SAIDA = PASTA / "MaratoNando-discloud.zip"

ARQUIVOS = [
    "bot.py", "db.py", "radar.py", "sugestao.py", "times.py", "biblioteca.py", "nomes.py",
    "marca.py", "cartoes.py", "notas.py", "estatisticas.py", "agenda.py", "jogadores.py", "modos.py", "ficha.py", "gratis.py", "requirements.txt", "discloud.config", ".env",
]
PASTAS = ["cogs", "servicos"]  # só os arquivos .py de dentro delas
# Imagens do Nando e fontes usadas nos cards (Manual da Marca). A pasta assets/export
# (avatar e banner para o Developer Portal) não vai: o bot não precisa dela para rodar.
ASSETS = {"assets/nando": "*.png", "assets/fontes": "*.ttf"}
BANCO = "maratonando.db"


def main() -> None:
    com_banco = "--com-banco" in sys.argv
    faltando = [a for a in ARQUIVOS if not (PASTA / a).exists()]
    if faltando:
        sys.exit(f"Faltam arquivos na pasta: {', '.join(faltando)}")

    with zipfile.ZipFile(SAIDA, "w", zipfile.ZIP_DEFLATED) as z:
        for nome in ARQUIVOS:
            z.write(PASTA / nome, nome)
        for pasta in PASTAS:
            for arquivo in sorted((PASTA / pasta).glob("*.py")):
                z.write(arquivo, f"{pasta}/{arquivo.name}")
        for pasta, padrao in ASSETS.items():
            arquivos = sorted((PASTA / pasta).glob(padrao))
            if not arquivos:
                sys.exit(f"A pasta {pasta} está vazia ou faltando (o bot precisa dela para os cards).")
            for arquivo in arquivos:
                z.write(arquivo, f"{pasta}/{arquivo.name}")
        if com_banco:
            if not (PASTA / BANCO).exists():
                sys.exit(f"Não achei o {BANCO} para incluir.")
            z.write(PASTA / BANCO, BANCO)

    with zipfile.ZipFile(SAIDA) as z:
        nomes = z.namelist()
    print(f"✅ {SAIDA.name} criado com {len(nomes)} arquivos:")
    for nome in nomes:
        print(f"   {nome}")
    print("\nCom o banco de dados (primeira subida)." if com_banco
          else "\nSem o banco de dados (atualização): o banco que está na Discloud continua intacto.")


if __name__ == "__main__":
    main()
