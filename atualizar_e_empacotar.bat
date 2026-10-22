@echo off
chcp 65001 >nul
rem Baixa a versão mais nova do GitHub e monta o zip da Discloud (sem o banco de dados).
cd /d "%~dp0"
echo 1/2 Baixando as novidades do GitHub...
git pull
if errorlevel 1 (
    echo.
    echo O git pull deu erro: leia a mensagem acima e mande para o Claude. O zip NAO foi montado.
    pause
    exit /b 1
)
echo.
echo 2/2 Montando o zip da Discloud...
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" empacotar_discloud.py
) else (
    python empacotar_discloud.py
)
echo.
echo Pronto! Agora envie o MaratoNando-discloud.zip na Discloud (opcao de atualizar / commit).
pause
