#!/usr/bin/env python3
"""
web_sourcing.py — internet-wide job search for Jarvis (separate from the ATS
pipeline in jarvis_sourcing.py).

Casts a wide net via the Adzuna jobs API, then holds every result to the SAME
quality bar as the ATS pipeline:
  - real post date from the API (created), <= max_age_days old
  - US only (Adzuna US index + location.area check)
  - title matches the resume-derived profile (reuses jarvis_sourcing gates)
  - not clearance/citizenship-gated (eligibility gate)
  - de-duplicated against the batch and the tracker
  - EVERY apply link is liveness-checked before delivery (dead links dropped)

Adzuna is free: get an app_id + app_key at https://developer.adzuna.com and put
them in the environment as ADZUNA_APP_ID / ADZUNA_APP_KEY.

Deps: requests (already required). Reuses jarvis_sourcing for the gates so the
two tools never drift apart.
"""

from __future__ import annotations

import datetime as dt
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests

import jarvis_sourcing as src

ADZUNA_BASE = "https://api.adzuna.com/v1/api/jobs/us/search"
HTTP_TIMEOUT = 20
_TAG = re.compile(r"<[^>]+>")


def adzuna_keys() -> tuple[str, str]:
    return os.environ.get("ADZUNA_APP_ID", ""), os.environ.get("ADZUNA_APP_KEY", "")


def _parse_dt(s: str | None) -> dt.datetime | None:
    if not s:
        return None
    try:
        return dt.datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except (ValueError, TypeError):
        return None


def search_adzuna(profile: dict, max_days: int = 14, per_page: int = 50,
                  log=print) -> list[dict]:
    """One query per resume title keyword; merged + de-duplicated by URL.
    Freshness (max_days_old) and US country are enforced by the API itself."""
    app_id, app_key = adzuna_keys()
    if not (app_id and app_key):
        raise RuntimeError("ADZUNA_APP_ID / ADZUNA_APP_KEY not set in the environment")

    seen: set[str] = set()
    out: list[dict] = []
    for kw in profile.get("title_include", []):
        params = {
            "app_id": app_id, "app_key": app_key,
            "results_per_page": per_page, "what_phrase": kw,
            "max_days_old": max_days, "sort_by": "date",
            "content-type": "application/json",
        }
        try:
            r = requests.get(f"{ADZUNA_BASE}/1", params=params, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            results = r.json().get("results", [])
        except Exception as e:
            log(f"[adzuna FAIL] '{kw}': {type(e).__name__}: {e}")
            continue
        for j in results:
            url = j.get("redirect_url", "")
            if not url or url in seen:
                continue
            seen.add(url)
            out.append({
                "title": _TAG.sub("", j.get("title", "")).strip(),
                "company": (j.get("company") or {}).get("display_name", "") or "",
                "location": (j.get("location") or {}).get("display_name", "") or "",
                "area": [a.lower() for a in (j.get("location") or {}).get("area", []) or []],
                "url": url,
                "posted": _parse_dt(j.get("created")),
                "description": _TAG.sub("", j.get("description", "")) or "",
            })
    return out


def _is_us(cand: dict) -> bool:
    # Adzuna puts the country in area[0]; when present it is authoritative.
    if cand["area"]:
        return "us" in cand["area"] or "united states" in cand["area"]
    return src._gate_location(cand["location"])          # fallback: ", TX" / "remote - us"


def verify_and_gate(cands: list[dict], prior_keys: set[str], profile: dict,
                    sess: requests.Session, log=print) -> tuple[list[dict], dict]:
    """Cheap gates first, then the parallel liveness check on survivors.
    Returns (delivered, stats) — delivered is freshest-first; every item's
    apply link returned < 400."""
    now = dt.datetime.now(dt.timezone.utc)
    stats = {k: 0 for k in ("fetched", "rej_stale", "rej_title", "rej_location",
                            "rej_eligibility", "rej_duplicate", "rej_dead")}
    stats["fetched"] = len(cands)

    survivors, seen = [], set(prior_keys)
    for c in cands:
        if not c["posted"] or (now - c["posted"]).days > profile["max_age_days"]:
            stats["rej_stale"] += 1;                         continue
        if not src._gate_title(c["title"], profile["title_include"], profile["title_exclude"]):
            stats["rej_title"] += 1;                         continue
        if not _is_us(c):
            stats["rej_location"] += 1;                      continue
        if not src._gate_eligibility(c["title"] + " " + c["description"],
                                     profile["eligibility_exclude"]):
            stats["rej_eligibility"] += 1;                   continue
        key = src.canonical(c["url"])
        if key in seen:
            stats["rej_duplicate"] += 1;                     continue
        seen.add(key)
        survivors.append(c)

    # the link-check step — verify every surviving apply URL resolves (<400)
    alive: set[str] = set()
    if survivors:
        with ThreadPoolExecutor(max_workers=10) as ex:
            futs = {ex.submit(src.url_live, sess, c["url"]): c for c in survivors}
            for f in as_completed(futs):
                if f.result():
                    alive.add(futs[f]["url"])

    delivered = []
    for c in survivors:
        if c["url"] in alive:
            delivered.append(c)
        else:
            stats["rej_dead"] += 1
    delivered.sort(key=lambda c: c["posted"], reverse=True)
    return delivered, stats
