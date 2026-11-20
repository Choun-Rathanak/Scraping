"""Export resolved Apache OFBiz Human Resources Jira issues as task data.

This script deliberately has no live collection side effect until it is run.
It can also transform a Jira CSV export when the REST API is unavailable.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable
from urllib import robotparser
from urllib.parse import urljoin

try:
    import requests
except ImportError:  # A clearer installation instruction than a traceback.
    requests = None


# Keep the task-duration dataset within the agreed collection window.  The
# upper bound is evaluated by Jira when the API query runs; CSV input is
# checked locally using the same rule below.
START_DATE = date(2024, 1, 1)
JQL = ("project = OFBIZ AND component = humanres AND resolution in "
       "(Fixed, Implemented) AND created >= 2024-01-01 AND created <= now() "
       "ORDER BY created ASC, key ASC")
OUTPUT_COLUMNS = [
    "Status", "Priority", "Created", "Resolved", "Labels", "Description",
    "title", "task_type", "assignee_id", "lead_time_days",
]
API_FIELDS = [
    "status", "priority", "created", "resolutiondate", "labels", "description",
    "summary", "issuetype", "assignee", "resolution",
]
USER_AGENT = "CADT-Intelligent-Task-Duration-Estimation/1.0 (educational API collector)"
# Component membership alone is noisy in this historic Jira project. These terms
# identify the HR features requested for the task-duration dataset.
HR_TOPIC_TERMS = (
    "leave", "attendance", "employee", "employment", "human resource", "humanres",
    "recruit", "applicant", "interview", "job requisition", "job posting", "payroll",
    "holiday", "timesheet", "time sheet", "employee position", "reporting structure",
    "benefit", "personnel", "performance review", "training",
)


def value(obj: Any, *names: str) -> Any:
    """Return a value from a dict, ignoring CSV-header capitalization."""
    if not isinstance(obj, dict):
        return None
    lowered = {str(key).casefold(): val for key, val in obj.items()}
    for name in names:
        found = lowered.get(name.casefold())
        if found not in (None, ""):
            return found
    return None


def jira_name(item: Any) -> str:
    return str(value(item, "name") or "") if isinstance(item, dict) else str(item or "")


def parse_date(raw: Any) -> datetime.date | None:
    """Parse Jira timestamps as their displayed calendar date (same rule both dates)."""
    if raw in (None, ""):
        return None
    text = str(raw).strip()
    try:
        # Dates are intentionally not converted to local/UTC time: both fields use
        # the calendar date Jira recorded, avoiding a timezone-dependent duration.
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return datetime.strptime(text[:10], "%Y-%m-%d").date()
        except ValueError:
            return None


def anonymous_assignee(recorded_id: Any) -> str:
    if recorded_id in (None, ""):
        return ""
    digest = hashlib.sha256(str(recorded_id).strip().encode("utf-8")).hexdigest()[:16]
    return f"ofbiz_hr_{digest}"


def clean_text(raw: Any) -> str:
    """Standardize line endings and outer whitespace without discarding content."""
    return str(raw or "").replace("\r\n", "\n").replace("\r", "\n").strip()


GREETING = re.compile(r"^(hello|hi|dear)\b", re.IGNORECASE)
SIGN_OFF = re.compile(r"^(thanks?(?:\s*(?:&|and)\s*regards)?|regards|best regards|kind regards|thank you)[\s,!.]*$", re.IGNORECASE)


def clean_description(raw: Any) -> str:
    """Make a one-line task description and remove email greeting/signature boilerplate."""
    kept: list[str] = []
    for line in clean_text(raw).replace("\u00a0", " ").split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if not line or GREETING.match(line):
            continue
        if SIGN_OFF.match(line):
            break  # A name after a sign-off is a signature, not task content.
        kept.append(line)
    return " ".join(kept)


def clean_labels(raw_labels: Any) -> str:
    """Keep real labels, in order, while removing blank or repeated labels."""
    labels = raw_labels or []
    if isinstance(labels, str):
        labels = labels.split(",")
    cleaned: list[str] = []
    seen: set[str] = set()
    for label in labels:
        text = clean_text(label)
        if text and text not in seen:
            cleaned.append(text)
            seen.add(text)
    return ",".join(cleaned)


def is_hr_topic(record: dict[str, Any]) -> bool:
    """Keep issues about requested HR features, not generic component maintenance."""
    fields = record.get("fields", record)
    labels = value(fields, "labels", "Labels") or []
    if isinstance(labels, list):
        labels = " ".join(map(str, labels))
    searchable = " ".join(str(value(fields, name) or "") for name in
                            ("summary", "Summary", "title", "description", "Description"))
    searchable = f"{searchable} {labels}".casefold()
    return any(term in searchable for term in HR_TOPIC_TERMS)


def issue_to_row(issue: dict[str, Any]) -> tuple[dict[str, str] | None, bool]:
    """Convert either an API issue or a normalized CSV issue. Second value is unassigned."""
    fields = issue.get("fields", issue)
    status = jira_name(value(fields, "status")) or str(value(fields, "Status") or "")
    resolution = jira_name(value(fields, "resolution")) or str(value(fields, "Resolution") or "")
    # CSV exports are expected to come from the documented JQL, but enforce a
    # populated resolution when it is present so the fallback remains aligned.
    if resolution and resolution not in {"Fixed", "Implemented"}:
        return None, False
    if status == "Closed" and resolution in {"Fixed", "Implemented"}:
        status = "Done"
    created = parse_date(value(fields, "created", "Created"))
    resolved = parse_date(value(fields, "resolutiondate", "Resolved", "Resolution Date"))
    if not created or not resolved or resolved < created:
        return None, False
    # Do not let CSV imports bypass the live-query date window, and exclude
    # accidentally future-dated records from either source.
    if not START_DATE <= created <= date.today():
        return None, False
    labels = value(fields, "labels", "Labels")
    assignee = value(fields, "assignee", "Assignee")
    if isinstance(assignee, dict):
        recorded_id = value(assignee, "accountId", "key", "name", "id")
    else:
        recorded_id = value(fields, "Assignee accountId", "Assignee ID", "assignee_id") or assignee
    issue_type = jira_name(value(fields, "issuetype")) or str(value(fields, "Issue Type", "Type") or "")
    task_type = {"Bug": "Bug Fix", "New Feature": "Feature Development"}.get(issue_type, issue_type)
    priority = jira_name(value(fields, "priority")) or str(value(fields, "Priority") or "")
    row = {
        "Status": clean_text(status), "Priority": clean_text(priority), "Created": created.isoformat(),
        "Resolved": resolved.isoformat(), "Labels": clean_labels(labels),
        "Description": clean_description(value(fields, "description", "Description")),
        "title": clean_text(value(fields, "summary", "Summary", "title")),
        "task_type": clean_text(task_type), "assignee_id": anonymous_assignee(recorded_id),
        "lead_time_days": str((resolved - created).days),
    }
    return row, not bool(recorded_id)


def inspect_robots(base_url: str, endpoint: str, timeout: int) -> float:
    """Lab 5-style robots check; return a declared crawl delay if available."""
    robots_url = urljoin(base_url.rstrip("/") + "/", "robots.txt")
    response = requests.get(robots_url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    print(f"robots.txt HTTP status: {response.status_code}")
    if response.status_code == 404:
        print("No robots rules published at this path; continuing cautiously with the official API.")
        return 0.0
    response.raise_for_status()
    parser = robotparser.RobotFileParser()
    parser.parse(response.text.splitlines())
    if not parser.can_fetch(USER_AGENT, endpoint):
        raise PermissionError("robots.txt disallows this API path. Stop collection and use --input-csv instead.")
    delay = parser.crawl_delay(USER_AGENT) or parser.crawl_delay("*") or 0.0
    print(f"robots.txt permits the API path; declared crawl delay: {delay} seconds.")
    return float(delay)


def api_issues(base_url: str, limit: int, timeout: int, delay_seconds: float,
               check_robots: bool) -> Iterable[dict[str, Any]]:
    if requests is None:
        raise RuntimeError("The requests package is missing. Run: pip install requests")
    endpoint = base_url.rstrip("/") + "/rest/api/2/search"
    robots_delay = inspect_robots(base_url, endpoint, timeout) if check_robots else 0.0
    effective_delay = max(delay_seconds, robots_delay)
    last_request_at: float | None = time.monotonic() if check_robots else None
    print(f"API collection limit: {limit}; request delay: {effective_delay} seconds.")
    start_at = 0
    yielded = 0
    while yielded < limit:
        params = {"jql": JQL, "startAt": start_at, "maxResults": min(50, limit - yielded),
                  "fields": ",".join(API_FIELDS)}
        for attempt in range(4):
            if last_request_at is not None:
                elapsed = time.monotonic() - last_request_at
                time.sleep(max(0.0, effective_delay - elapsed))
            response = requests.get(endpoint, params=params, timeout=timeout,
                                    headers={"Accept": "application/json", "User-Agent": USER_AGENT})
            last_request_at = time.monotonic()
            if response.status_code == 429 and attempt < 3:
                delay = int(response.headers.get("Retry-After", "1"))
                print(f"Rate limited; retrying in {delay} seconds...", file=sys.stderr)
                time.sleep(max(1, delay))
                continue
            if response.status_code in (401, 403):
                raise PermissionError("Jira access was denied. Export the search as CSV and run with --input-csv.")
            response.raise_for_status()
            print(f"Jira API HTTP {response.status_code}; page starts at issue {start_at}.")
            break
        else:
            raise RuntimeError("Jira continued rate-limiting requests after retries.")
        data = response.json()
        batch = data.get("issues", [])
        if not batch:
            return
        for issue in batch:
            yield issue
            yielded += 1
            if yielded >= limit:
                return
        start_at += len(batch)
        if start_at >= data.get("total", 0):
            return


def csv_issues(path: Path) -> Iterable[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        yield from csv.DictReader(handle)


def export(issues: Iterable[dict[str, Any]], output: Path, topic_filter: bool = True) -> dict[str, int]:
    stats = {"exported": 0, "skipped": 0, "filtered_out": 0, "duplicates": 0, "missing_assignees": 0}
    seen: set[str] = set()
    with output.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS, quoting=csv.QUOTE_MINIMAL)
        writer.writeheader()
        for issue in issues:
            key = str(value(issue, "key", "Issue key", "Issue Key") or "")
            if key and key in seen:
                stats["duplicates"] += 1
                continue
            if key:
                seen.add(key)
            if topic_filter and not is_hr_topic(issue):
                stats["filtered_out"] += 1
                continue
            row, unassigned = issue_to_row(issue)
            if row is None:
                stats["skipped"] += 1
                continue
            writer.writerow(row)
            stats["exported"] += 1
            stats["missing_assignees"] += int(unassigned)
    return stats


def clean_task_csv(source: Path, output: Path) -> dict[str, int]:
    """Filter an already exported task CSV without altering anonymous IDs."""
    stats = {"exported": 0, "filtered_out": 0}
    with source.open("r", encoding="utf-8-sig", newline="") as source_file:
        reader = csv.DictReader(source_file)
        if reader.fieldnames != OUTPUT_COLUMNS:
            raise ValueError("--clean-output-csv expects a CSV created by this script.")
        with output.open("w", encoding="utf-8", newline="") as output_file:
            writer = csv.DictWriter(output_file, fieldnames=OUTPUT_COLUMNS, quoting=csv.QUOTE_MINIMAL)
            writer.writeheader()
            for row in reader:
                if is_hr_topic(row):
                    row["Labels"] = clean_labels(row["Labels"])
                    row["Description"] = clean_description(row["Description"])
                    row["title"] = clean_text(row["title"])
                    row["Status"] = clean_text(row["Status"])
                    row["Priority"] = clean_text(row["Priority"])
                    row["task_type"] = clean_text(row["task_type"])
                    writer.writerow(row)
                    stats["exported"] += 1
                else:
                    stats["filtered_out"] += 1
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description="Export resolved OFBiz humanres Jira issues.")
    source_group = parser.add_mutually_exclusive_group()
    source_group.add_argument("--input-csv", type=Path, help="Local Jira CSV export (no network access).")
    source_group.add_argument("--clean-output-csv", type=Path,
                              help="Clean an existing ofbiz_hr_tasks.csv without changing its IDs.")
    parser.add_argument("--output", type=Path, default=Path("ofbiz_hr_tasks.csv"))
    parser.add_argument("--limit", type=int, default=50, help="Maximum API issues to retrieve (default: 50).")
    parser.add_argument("--base-url", default="https://issues.apache.org/jira", help="Jira base URL.")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds.")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="Minimum seconds between API requests (default: 0.5).")
    parser.add_argument("--skip-robots-check", action="store_true",
                        help="Skip the robots.txt check only when you have separately verified permission.")
    parser.add_argument("--no-topic-filter", action="store_true",
                        help="Keep all issues returned by Jira's humanres component query.")
    args = parser.parse_args()
    if args.limit < 1 or args.delay < 0:
        parser.error("--limit must be at least 1 and --delay cannot be negative")
    try:
        if args.clean_output_csv:
            stats = clean_task_csv(args.clean_output_csv, args.output)
            print("Exported {exported} HR-topic rows; filtered out {filtered_out} unrelated rows.".format(**stats))
            print(f"Wrote {args.output}")
            return 0
        # Check before opening the output file, so a missing dependency never
        # leaves behind a misleading header-only CSV.
        if not args.input_csv and requests is None:
            raise RuntimeError("The requests package is missing. Run: pip install requests")
        issues = csv_issues(args.input_csv) if args.input_csv else api_issues(
            args.base_url, args.limit, args.timeout, args.delay, not args.skip_robots_check)
        stats = export(issues, args.output, topic_filter=not args.no_topic_filter)
    except (OSError, RuntimeError, PermissionError, requests.RequestException if requests else Exception) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    print("Exported {exported} issues; skipped {skipped} rows; filtered out {filtered_out}; duplicates {duplicates}; missing assignees {missing_assignees}.".format(**stats))
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
