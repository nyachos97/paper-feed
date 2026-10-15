@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
echo === 논문 PDF 도우미 설치 ===

where pythonw >nul 2>nul
if errorlevel 1 (
  echo [오류] Python이 없습니다. https://www.python.org/downloads/ 에서 설치하고
  echo        설치 화면의 "Add python.exe to PATH"를 꼭 체크한 뒤 다시 실행하세요.
  pause
  exit /b 1
)

if not exist helper_config.json (
  python paper_pdf_helper.py --once >nul 2>nul
)

echo.
echo 1) helper_config.json 을 메모장으로 엽니다. "github_repo" 에 저장소 이름(예: 아이디/paper-feed)을 넣고 저장하세요.
start /wait notepad helper_config.json

echo 2) 윈도우 시작 시 자동 실행 등록...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$s=(New-Object -ComObject WScript.Shell).CreateShortcut([Environment]::GetFolderPath('Startup')+'\논문 PDF 도우미.lnk');" ^
  "$s.TargetPath=(Get-Command pythonw).Source; $s.Arguments='\"%~dp0paper_pdf_helper.py\"'; $s.WorkingDirectory='%~dp0'; $s.Save()"

echo 3) 지금 바로 시작합니다.
start "" pythonw "%~dp0paper_pdf_helper.py"

echo.
echo 완료! 기록은 helper.log, 자동 저장이 안 된 논문은 저장 폴더의 _직접받기_목록.html 에서 볼 수 있습니다.
pause
