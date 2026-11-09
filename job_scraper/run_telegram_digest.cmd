@echo off
setlocal
cd /d C:\Users\adamc\Desktop\ai-job-search
"C:\Users\adamc\AppData\Local\Programs\Python\Python312\python.exe" job_scraper\telegram_notify.py digest >> daily\telegram_digest.log 2>&1
exit /b %ERRORLEVEL%
