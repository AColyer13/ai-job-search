' Runs run_telegram_digest.cmd with no console window and waits for it, so Task Scheduler sees it finish.
CreateObject("WScript.Shell").Run """C:\Users\adamc\Desktop\ai-job-search\job_scraper\run_telegram_digest.cmd""", 0, True
