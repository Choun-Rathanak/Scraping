# OFBiz HR task scraper

`scrape_ofbiz_hr.py` exports resolved Apache OFBiz Human Resources Jira issues in a compact task-duration format. It uses the requested JQL and only asks the API for output-related fields. Tasks are included only when their **Created** date is from 2024-01-01 through the day the script runs; this rule applies to both API collection and CSV imports. The script does not run live collection during development; use the CSV mode for reviewable, offline runs.

## Install

Requires Python 3.10+.

```powershell
py -3.13 -m pip install requests
```

## Run with a Jira CSV export

Export the matching Jira search to CSV, then run:

```powershell
py -3.13 scrape_ofbiz_hr.py --input-csv path\to\jira-export.csv
```

This creates `ofbiz_hr_tasks.csv`. The fixture can be tried safely:

```powershell
py -3.13 scrape_ofbiz_hr.py --input-csv fixtures\ofbiz_hr_sample.csv --output sample_output.csv
```

## Run against the API

Only run this after source approval:

```powershell
py -3.13 scrape_ofbiz_hr.py --limit 100
```

`--limit` is 50 by default. API results paginate until that cap, use a 30-second timeout, retry 429 responses based on `Retry-After`, and give a CSV-export suggestion for denied access. Following the Lab 5 collection approach, live API mode identifies the educational client, checks `robots.txt`, stops if the API path is disallowed, and waits at least 0.5 seconds between requests (or a longer declared crawl delay). Use `--skip-robots-check` only when permission was independently verified.

## Clean the current output

The historic `humanres` Jira component also contains generic work such as i18n and license changes. The scraper now keeps only issues whose title, description, or labels contain HR-feature terms (for example `leave`, `attendance`, `employee`, `employment`, `recruitment`, `payroll`, or `holiday`). To clean an output already created before this filter was added, preserving its anonymous IDs:

```powershell
py -3.13 scrape_ofbiz_hr.py --clean-output-csv ofbiz_hr_tasks.csv --output ofbiz_hr_tasks_cleaned.csv
```

Use `--no-topic-filter` only if you intentionally want every issue from the broad Jira component query.

## Dataset-compatible preprocessing

`dataset.csv` and this scraper use the same required ten-column schema and column order. Before writing, the scraper validates dates and non-negative lead time, standardizes text line endings and surrounding whitespace, removes blank/repeated labels, maps `Closed` + `Fixed`/`Implemented` to `Done`, and maps `Bug`/`New Feature` to the requested task types. Jira priority values remain unchanged, and Jira assignees are hashed as `ofbiz_hr_...`; this intentionally prevents them from being confused with the numeric synthetic IDs in `dataset.csv`.

To create a separate file ready to append to your dataset without changing `dataset.csv` itself:

```powershell
py -3.13 scrape_ofbiz_hr.py --limit 50 --output ofbiz_hr_tasks_preprocessed.csv
```

## Jupyter preprocessing workflow

Open `OFBiz_HR_Preprocessing.ipynb` and run its cells from top to bottom. It follows the Lab 5 sequence: load the scrape, inspect the records, identify HR-topic rows, clean and validate dates/labels/duplicates, preview the result, then save `ofbiz_hr_tasks_preprocessed.csv` in the final cell. It checks that the saved header exactly matches `dataset.csv` and does not modify `dataset.csv`.

## Local test

```powershell
py -3.13 -m unittest -v
```

The fixture checks a six-day task, a same-day task, a missing assignee, a duplicate issue, and a negative-date row that must be skipped.
