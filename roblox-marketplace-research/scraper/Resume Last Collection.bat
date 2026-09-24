@echo off
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel% equ 0 (
    py -3 roblox_scraper.py --resume
) else (
    python roblox_scraper.py --resume
)
pause
