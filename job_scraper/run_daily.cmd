@echo off
setlocal
set "PATH=C:\Users\adamc\.bun\bin;%PATH%"
cd /d C:\Users\adamc\Desktop\ai-job-search
"C:\Users\adamc\AppData\Local\Programs\Python\Python312\python.exe" job_scraper\daily_digest.py
exit /b %ERRORLEVEL%
