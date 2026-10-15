@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo GitHub 후보 목록을 확인해 PDF를 받습니다...
python paper_pdf_helper.py --once
echo.
powershell -NoProfile -Command "Get-Content -Tail 20 -Encoding UTF8 helper.log"
pause
