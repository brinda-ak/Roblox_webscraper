@echo off
cd /d "%~dp0"
echo Roblox / Minecraft tag analyzer
echo First setup downloads Python libraries and an AI model. Allow a few minutes.
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" -c "import sentence_transformers, umap, plotly, sklearn" >nul 2>&1
if errorlevel 1 (
  ".venv\Scripts\python.exe" -m pip install -r requirements.txt
  if errorlevel 1 goto failed
)
".venv\Scripts\python.exe" analyze.py
pause
exit /b
:failed
echo Setup failed. Use Python 3.12 or 3.13 from python.org and an internet connection.
pause
exit /b 1
