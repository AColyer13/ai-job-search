# /html-report - Generate Application Tracker Dashboard

Write `reports/application-dashboard.html` from the same job list as `daily/latest.docx`, plus the tracker and the draft gap.

The daily publish already writes this file. Run the script when you only need to refresh the page:

```bash
python job_scraper/html_report.py
```

No argument writes `reports/application-dashboard.html`. Do not hand-write a second dashboard. The script is the page.

The page is one file. CSS and the filter script are inline. It does not load a CDN. Job text, tracker notes, and draft names are HTML-escaped.

The jobs section uses the Word list: high and medium by position type, lows at the bottom, the same why line, and the same title-match grades. Search, grade, and type filter the cards in the browser. The applications section lists `job_search_tracker.csv`. Draft CV and cover files with no tracker row are named underneath. The script does not invent a date, a status, or a fit rating, and it does not add tracker rows.

Open the file in a browser. No server is required.
