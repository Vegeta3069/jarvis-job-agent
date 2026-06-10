#!/usr/bin/env python3
"""
jarvis_sourcing.py — v2 sourcing engine for Jarvis.

Replaces DDGS/SERP scraping. Sources ONLY from sponsor companies' own ATS
feeds (greenhouse / lever / ashby / workday-CXS). No web search anywhere.
If the day's yield is short, the list is short; never padded.

Exposes building blocks the MCP server orchestrates:
  load_config(path)                  -> config dict
  fetch_all(cfg, sess)               -> (raw jobs, source_stats)
  apply_gates(jobs, known_urls)      -> (passed jobs, gate_stats)
  verify_live(sess, jobs)            -> jobs whose URL resolves (<400)
  canonical(url)                     -> dedup key (query string stripped)

Deps: requests, pyyaml
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable

import requests
import yaml

MAX_AGE_DAYS = 14
HTTP_TIMEOUT = 15
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"}

TITLE_INCLUDE = ["sre", "site reliability", "devops", "platform engineer",
                 "infrastructure engineer", "cloud engineer", "reliability engineer",
                 "devsecops"]
TITLE_EXCLUDE = ["intern", "manager", "director", "principal architect",
                 "equipment reliability", "plant reliability", "maintenance"]
ELIGIBILITY_EXCLUDE = ["security clearance", "ts/sci", "top secret",
                       "us citizenship required", "u.s. citizenship", "itar",
                       "active clearance", "secret clearance", "public trust",
                       "polygraph", "clearance"]
US_HINTS = ["united states", "usa", ", us", "remote - us", "us remote",
            "remote (us", "u.s.", "us-remote", "remote, us"]
US_STATE = re.compile(r",\s*[A-Z]{2}(\b|$)")          # "Austin, TX"

ALLOWED_HOSTS = ("greenhouse.io", "lever.co", "ashbyhq.com", "myworkdayjobs.com")

# ── role profile ─────────────────────────────────────────────────────────────
# The sponsor company list (sponsors.yaml) is fixed; WHICH roles get matched is
# driven by the user's resume. setup_profile (MCP tool) derives this once via
# the Anthropic API and writes profile.yaml. Swap resume + re-run to re-target.
# Constants above are the fallback used until profile.yaml exists.
DEFAULT_PROFILE = {
    "target_role": "DevOps / SRE / Platform Engineer",
    "title_include": TITLE_INCLUDE,
    "title_exclude": TITLE_EXCLUDE,
    "eligibility_exclude": ELIGIBILITY_EXCLUDE,
    "max_age_days": MAX_AGE_DAYS,
}


def load_profile(path: str | None = None) -> dict:
    """Role filter for the gates. Reads profile.yaml if present, else falls back
    to DEFAULT_PROFILE. Anyone repoints the whole search just by swapping their
    resume and re-running setup_profile."""
    prof = dict(DEFAULT_PROFILE)
    p = path or os.path.join(os.path.dirname(__file__), "profile.yaml")
    if os.path.exists(p):
        try:
            data = yaml.safe_load(open(p)) or {}
            for k in DEFAULT_PROFILE:
                if data.get(k):
                    prof[k] = data[k]
        except Exception as e:
            print(f"[WARN] profile.yaml unreadable ({e}); using default filter", file=sys.stderr)
    return prof


@dataclasses.dataclass
class Job:
    company: str
    title: str
    location: str
    url: str
    ats: str
    employer_type: str                  # direct | consultancy
    posted_at: dt.datetime | None       # tz-aware UTC
    apply_mode: str = "manual"          # auto (greenhouse) | manual

    @property
    def age_days(self) -> float | None:
        if not self.posted_at:
            return None
        return (dt.datetime.now(dt.timezone.utc) - self.posted_at).total_seconds() / 86400


def canonical(url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit
    p = urlsplit(url)
    return urlunsplit((p.scheme, p.netloc, p.path, "", ""))


def allowed_domain(url: str) -> bool:
    from urllib.parse import urlsplit
    return any(h in urlsplit(url).netloc for h in ALLOWED_HOSTS)


def _iso(v: str | None) -> dt.datetime | None:
    if not v:
        return None
    try:
        return dt.datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except (ValueError, TypeError):
        return None


# ───────────────────────────── adapters ─────────────────────────────────────
def gh(sess, c) -> list[Job]:
    r = sess.get(f"https://boards-api.greenhouse.io/v1/boards/{c['token']}/jobs?content=false",
                 timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    # Canonical board URL, NOT absolute_url — for many companies the latter is
    # their own careers domain (stripe.com, databricks.com), which fails the
    # ATS-host allowlist and silently drops the posting.
    return [Job(c["name"], j.get("title", ""), (j.get("location") or {}).get("name", ""),
                f"https://boards.greenhouse.io/{c['token']}/jobs/{j['id']}",
                "greenhouse", c.get("type", "direct"),
                _iso(j.get("updated_at")), apply_mode="auto")
            for j in r.json().get("jobs", []) if j.get("id")]


def lever(sess, c) -> list[Job]:
    r = sess.get(f"https://api.lever.co/v0/postings/{c['token']}?mode=json",
                 timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    out = []
    for j in r.json():
        ts = j.get("createdAt")
        posted = (dt.datetime.fromtimestamp(ts / 1000, dt.timezone.utc)
                  if isinstance(ts, (int, float)) else None)
        out.append(Job(c["name"], j.get("text", ""),
                       (j.get("categories") or {}).get("location", "") or "",
                       j.get("hostedUrl", ""), "lever", c.get("type", "direct"), posted))
    return out


def ashby(sess, c) -> list[Job]:
    r = sess.get(f"https://api.ashbyhq.com/posting-api/job-board/{c['token']}",
                 timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return [Job(c["name"], j.get("title", ""), j.get("location", "") or "",
                j.get("jobUrl", ""), "ashby", c.get("type", "direct"),
                _iso(j.get("publishedAt")))
            for j in r.json().get("jobs", [])]


_WD_AGE = re.compile(r"posted\s+(\d+)\+?\s+days?\s+ago", re.I)

def _wd_age_days(posted_on: str) -> float | None:
    s = (posted_on or "").lower()
    if "today" in s:
        return 0.0
    if "yesterday" in s:
        return 1.0
    m = _WD_AGE.search(s)
    if m:
        days = float(m.group(1))
        return days + 15.0 if "+" in s else days   # "30+ days" -> stale, rejected
    return None                                     # unknown -> rejected by gate


def workday(sess, c) -> list[Job]:
    """Unofficial CXS endpoint. Config needs: tenant, host (wdN), site."""
    base = f"https://{c['tenant']}.{c['host']}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{c['tenant']}/{c['site']}/jobs"
    out, offset = [], 0
    while offset <= 80:                              # 5 pages max per company
        body = {"appliedFacets": {}, "limit": 20, "offset": offset,
                "searchText": c.get("search", "devops sre platform reliability")}
        r = sess.post(api, json=body, timeout=HTTP_TIMEOUT)
        r.raise_for_status()
        posts = r.json().get("jobPostings", [])
        if not posts:
            break
        for j in posts:
            age = _wd_age_days(j.get("postedOn", ""))
            posted = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=age)
                      if age is not None else None)
            out.append(Job(c["name"], j.get("title", ""),
                           j.get("locationsText", "") or "",
                           base + "/en-US/" + c["site"] + j.get("externalPath", ""),
                           "workday", c.get("type", "direct"), posted))
        offset += 20
    return out


ADAPTERS: dict[str, Callable] = {"greenhouse": gh, "lever": lever,
                                 "ashby": ashby, "workday": workday}


# ───────────────────────────── pipeline pieces ──────────────────────────────
def new_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(UA)
    return s


def load_config(path: str) -> dict:
    with open(path) as f:
        return yaml.safe_load(f)


def _fetch_one(sess: requests.Session, c: dict):
    """Fetch a single company. Returns (company, jobs|None, error|None)."""
    fn = ADAPTERS.get(c.get("ats"))
    if not fn:
        return c, None, "unknown ats"
    try:
        return c, fn(sess, c), None
    except Exception as e:
        return c, None, f"{type(e).__name__}: {e}"


def fetch_all(cfg: dict, sess: requests.Session) -> tuple[list[Job], dict]:
    """Fetch every company in parallel. One slow/broken feed never blocks or
    aborts the rest — it just logs loudly and is counted as a failed source."""
    companies = cfg.get("companies", [])
    raw, stats = [], {"sources_ok": 0, "sources_failed": 0, "failed_names": []}
    with ThreadPoolExecutor(max_workers=12) as ex:
        futs = [ex.submit(_fetch_one, sess, c) for c in companies]
        for f in as_completed(futs):
            c, got, err = f.result()
            if err is None:
                print(f"[OK]   {c['name']:<18} {len(got):>4}", file=sys.stderr)
                raw.extend(got)
                stats["sources_ok"] += 1
            else:
                print(f"[FAIL] {c['name']:<18} {err}", file=sys.stderr)
                stats["sources_failed"] += 1
                stats["failed_names"].append(c.get("name", "?"))
    return raw, stats


def _gate_title(t: str, include: list[str], exclude: list[str]) -> bool:
    t = t.lower()
    return any(k in t for k in include) and not any(x in t for x in exclude)


def _gate_location(loc: str) -> bool:
    if not loc:
        return False                                  # NEVER default to USA
    low = loc.lower()
    return any(h in low for h in US_HINTS) or bool(US_STATE.search(loc))


_ELIG_NOT_REQUIRED = re.compile(
    r"\b(no|not|without|don'?t|do not|doesn'?t|isn'?t)\b[^.\n]{0,25}"
    r"\b(clearance|polygraph|citizenship)\b"
    r"|\b(clearance|polygraph|citizenship)\b[^.\n]{0,25}\bnot\b[^.\n]{0,12}"
    r"\b(required|needed|necessary)\b")


def _gate_eligibility(t: str, exclude: list[str]) -> bool:
    """Reject clearance / citizenship / ITAR-gated roles (visa-ineligible for an
    H-1B holder). A posting that explicitly says such a requirement is NOT needed
    still passes — so 'clearance' is caught, but 'clearance not required' isn't."""
    t = t.lower()
    if not any(x in t for x in exclude):
        return True
    return bool(_ELIG_NOT_REQUIRED.search(t))


def passes_content_gates(title: str, location: str, profile: dict | None = None) -> bool:
    """Title + location + eligibility gates only — no freshness/domain/dedup.
    Used to re-screen carried-over rows whose posting date is unknown."""
    p = profile or DEFAULT_PROFILE
    return (_gate_title(title, p["title_include"], p["title_exclude"])
            and _gate_location(location)
            and _gate_eligibility(title, p["eligibility_exclude"]))


def apply_gates(jobs: list[Job], known_urls: set[str],
                profile: dict | None = None) -> tuple[list[Job], dict]:
    """Order: domain, dedup, freshness, title, location, eligibility.
    `profile` (from load_profile) supplies the resume-derived title filter."""
    p = profile or DEFAULT_PROFILE
    stats = {k: 0 for k in ["rej_domain", "rej_duplicate", "rej_stale",
                            "rej_title", "rej_location", "rej_eligibility"]}
    passed, seen = [], set()
    for j in jobs:
        cu = canonical(j.url)
        if not j.url or not allowed_domain(j.url):
            stats["rej_domain"] += 1;        continue
        if cu in known_urls or cu in seen:
            stats["rej_duplicate"] += 1;     continue
        if j.age_days is None or j.age_days > p["max_age_days"]:
            stats["rej_stale"] += 1;         continue
        if not _gate_title(j.title, p["title_include"], p["title_exclude"]):
            stats["rej_title"] += 1;         continue
        if not _gate_location(j.location):
            stats["rej_location"] += 1;      continue
        if not _gate_eligibility(j.title, p["eligibility_exclude"]):
            stats["rej_eligibility"] += 1;   continue
        seen.add(cu)
        passed.append(j)
    return passed, stats


def url_live(sess: requests.Session, url: str) -> bool:
    try:
        r = sess.head(url, timeout=HTTP_TIMEOUT, allow_redirects=True)
        if r.status_code in (403, 405):               # some ATS dislike HEAD
            r = sess.get(url, timeout=HTTP_TIMEOUT, allow_redirects=True, stream=True)
        return r.status_code < 400
    except requests.RequestException:
        return False


def verify_live(sess: requests.Session, jobs: list[Job]) -> list[Job]:
    if not jobs:
        return []
    live = []
    with ThreadPoolExecutor(max_workers=10) as ex:
        futs = {ex.submit(url_live, sess, j.url): j for j in jobs}
        for f in as_completed(futs):
            if f.result():
                live.append(futs[f])
    return live


def rank(jobs: list[Job]) -> list[Job]:
    """Direct employers above consultancies, then freshest first."""
    return sorted(jobs, key=lambda j: (j.employer_type != "direct",
                                       j.age_days if j.age_days is not None else 99))
