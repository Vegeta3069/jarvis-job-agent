#!/usr/bin/env python3
"""
Mark jobs as applied in the tracker.
Usage:
  python apply.py list              — show today's unapplied jobs
  python apply.py list all          — show all unapplied jobs
  python apply.py apply <row#>      — mark row N as applied
  python apply.py stats             — show application stats
"""

import csv
import sys
from datetime import date
from pathlib import Path

TRACKER_FILE = Path(__file__).parent / "tracker.csv"
TODAY = date.today().strftime("%Y-%m-%d")


def load_rows() -> list[dict]:
    if not TRACKER_FILE.exists():
        print("No tracker file found. Run job_agent.py first.")
        sys.exit(1)
    with open(TRACKER_FILE, "r") as f:
        return list(csv.DictReader(f))


def save_rows(rows: list[dict]):
    with open(TRACKER_FILE, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=[
            "date_found", "title", "company", "location",
            "link", "status", "date_applied", "notes"
        ])
        writer.writeheader()
        writer.writerows(rows)


def cmd_list(show_all: bool = False):
    rows = load_rows()
    pending = [
        (i + 2, r) for i, r in enumerate(rows)  # row# = line in CSV (1-indexed header + 1)
        if r.get("status") != "applied"
        and (show_all or r.get("date_found") == TODAY)
    ]
    if not pending:
        label = "all time" if show_all else "today"
        print(f"No unapplied jobs for {label}.")
        return
    label = "ALL unapplied" if show_all else f"Today's unapplied ({TODAY})"
    print(f"\n{label} — {len(pending)} jobs:\n")
    for row_num, job in pending:
        print(f"  [{row_num:3d}] {job['title']} @ {job['company']}")
        print(f"        {job['location']} | {job['date_found']}")
        print(f"        {job['link']}\n")


def cmd_apply(row_num: int, notes: str = ""):
    rows = load_rows()
    idx = row_num - 2  # subtract header row and 1-indexing
    if idx < 0 or idx >= len(rows):
        print(f"Invalid row number: {row_num}. Valid range: 2–{len(rows)+1}")
        sys.exit(1)
    job = rows[idx]
    if job.get("status") == "applied":
        print(f"Already marked applied: {job['title']} @ {job['company']}")
        return
    rows[idx]["status"] = "applied"
    rows[idx]["date_applied"] = TODAY
    rows[idx]["notes"] = notes
    save_rows(rows)
    print(f"✅ Marked as applied: {job['title']} @ {job['company']}")


def cmd_stats():
    rows = load_rows()
    total = len(rows)
    applied = [r for r in rows if r.get("status") == "applied"]
    by_date: dict[str, int] = {}
    for r in applied:
        d = r.get("date_applied", "unknown")
        by_date[d] = by_date.get(d, 0) + 1

    print(f"\n{'='*45}")
    print(f"  APPLICATION TRACKER STATS")
    print(f"{'='*45}")
    print(f"  Total jobs in tracker : {total}")
    print(f"  Applied               : {len(applied)}")
    print(f"  Not yet applied       : {total - len(applied)}")
    if by_date:
        print(f"\n  Applications per day:")
        for d in sorted(by_date):
            bar = "█" * by_date[d]
            print(f"    {d}  {bar}  ({by_date[d]})")
    print(f"{'='*45}\n")


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "list":
        show_all = len(args) > 1 and args[1] == "all"
        cmd_list(show_all)
    elif args[0] == "apply" and len(args) >= 2:
        notes = " ".join(args[3:]) if len(args) > 3 else ""
        cmd_apply(int(args[1]), notes)
    elif args[0] == "stats":
        cmd_stats()
    else:
        print(__doc__)
