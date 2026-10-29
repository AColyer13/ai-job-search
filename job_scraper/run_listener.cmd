@echo off
rem Always-on Telegram listener for the apply agent. Started at logon by the "Adam apply listener" task
rem (through run_listener_hidden.vbs so no window shows). If it stops or crashes it starts again after 30 seconds.
rem A second copy exits at once because the first holds daily\apply_agent.lock.
setlocal
set "PATH=C:\Users\adamc\.bun\bin;%PATH%"
set "PYTHONIOENCODING=utf-8"
cd /d C:\Users\adamc\Desktop\ai-job-search
:loop
"C:\Users\adamc\AppData\Local\Programs\Python\Python312\python.exe" job_scraper\apply_agent.py listen >> daily\apply_agent.log 2>&1
ping -n 31 127.0.0.1 >nul
goto loop
