@echo off
chcp 65001 >nul
del "%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\논문 PDF 도우미.lnk" 2>nul
echo 자동 실행 등록을 지웠습니다. (실행 중인 도우미는 작업 관리자에서 pythonw를 끝내거나 PC를 재시작하세요)
pause
