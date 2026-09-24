#!/bin/zsh
cd -- "${0:A:h}" || exit 1
if command -v python3 >/dev/null 2>&1; then
  python3 roblox_scraper.py
else
  print 'Python 3.10 or newer is required: https://www.python.org/downloads/'
fi
print '\nPress Enter to close.'
read -r
