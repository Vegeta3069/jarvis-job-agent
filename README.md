# Jarvis — Résumé-Driven Job Hunter

Jarvis is a personal job-sourcing agent that runs as an **MCP server inside Claude
Desktop**. You drop in your résumé, and it surfaces fresh, real, apply-ready job
postings for **your** role from a curated list of companies — pulled straight from
those companies' official Applicant Tracking System (ATS) feeds, never from web
search or job-board scraping.

> **Résumé in → matched jobs out.** The company list is fixed; *which roles* get
> matched is derived from your résumé. Swap the résumé, re-run setup, and the same
> pipeline now hunts for a different role.

---

## Why it exists

Typical "job search" automation scrapes LinkedIn/Indeed/Google and saves a pile of
dead links, ad redirects, duplicates, and stale postings. Jarvis takes the opposite
approach:

- **Sources only from official ATS APIs** (Greenhouse, Lever, Ashby, Workday). These
  return *currently-open* requisitions with canonical apply URLs and real timestamps,
  so dead links are structurally almost impossible.
- **Validates every posting through 7 gates** before it ever reaches you.
- **Never pads.** A thin day is a thin list — it tells you honestly what it rejected
  and why, instead of inventing filler.

---

## How it works

```
         your résumé                    sponsors.yaml (curated companies)
              │                                      │
        setup_profile                          fetch_all (ATS APIs)
   (LLM derives role filter)                         │
              │                                  raw postings
        profile.yaml ───────────────┐                │
                                     ▼                ▼
                              ┌──────────────────────────────┐
                              │  apply_gates (in order):      │
                              │  1 domain  2 dedup  3 fresh    │
                              │  4 title*  5 US-loc  6 elig    │   * from your résumé
                              └──────────────┬───────────────┘
                                             ▼
                                   liveness check (HTTP)
                                             ▼
                              carryover re-check + cap (60)
                                             ▼
                          tracker.csv  +  jobs/<date>_jobs.md digest
```

Each day (`find_jobs` / "wake up Jarvis"):

1. **Carryover** — every unapplied job already in your tracker is re-screened against
   your current role filter and re-checked for liveness; closed reqs are marked closed.
2. **Fresh pull** — every company in `sponsors.yaml` is fetched from its ATS.
3. **Gates** — domain allowlist → dedup → freshness → **title (your résumé filter)** →
   US location → eligibility.
4. **Liveness** — every surviving apply URL is HTTP-checked in parallel.
5. **Deliver** — carryover + new, ranked freshest-first (direct employers above
   consultancies), capped, with a full rejection breakdown. Written to the tracker and
   a Markdown digest.

### The 7 gates

| # | Gate | Rejects |
|---|------|---------|
| 1 | Domain allowlist | anything not on greenhouse.io / lever.co / ashbyhq.com / myworkdayjobs.com |
| 2 | Dedup | canonical-URL duplicates (query strings stripped), within the batch and vs. the tracker |
| 3 | Freshness | postings older than `max_age_days` (default 14), using the ATS timestamp — never scrape time |
| 4 | Title | titles that don't match **your résumé's role filter** (`profile.yaml`) |
| 5 | US location | non-US / unparseable locations (never defaults to "USA") |
| 6 | Eligibility | clearance / TS-SCI / citizenship-required / ITAR (visa-ineligible) |
| 7 | Liveness | apply URLs returning ≥400 or failing to resolve |

---

## The tools (in Claude Desktop)

| Tool | What it does |
|------|--------------|
| `setup_profile` | Reads your résumé, derives your role filter → `profile.yaml`. **Run this first.** |
| `find_jobs` | The daily ATS pipeline above (your curated company list). Trigger: *"wake up Jarvis."* |
| `search_web_jobs` | **Internet-wide** search via the Adzuna API — *separate* from `find_jobs`. Same quality bar (profile-matched, ≤ `max_age_days`, US, deduped) and **every apply link is liveness-checked before delivery**. Needs `ADZUNA_APP_ID`/`ADZUNA_APP_KEY`. Trigger: *"search the web."* |
| `list_jobs` | List tracker rows (`today` / `all` / `pending` / a date). |
| `mark_applied(n)` | Mark tracker row #n applied. |
| `get_stats` | Totals, per-day found/applied, pipeline health. |
| `open_digest` | Path to a day's Markdown digest. |
| `tailor_resume(n)` | Generate a job-specific résumé DOCX for row #n (re-emphasis of real experience only — never fabricates). |

---

## Two search modes

| | `find_jobs` (default) | `search_web_jobs` (opt-in) |
|---|---|---|
| Source | Your curated `sponsors.yaml` ATS feeds | The open web, via the Adzuna jobs API |
| Reach | Exactly the companies you list | Internet-wide |
| Junk control | Structurally clean (ATS-only) | Gated + **every link liveness-checked** |
| Needs a key | No | Yes (free Adzuna key) |

Both apply the **same** résumé-derived role filter, the same freshness/US/eligibility
gates, the same dedup, and the same live-link verification — so the wide-net mode
stays clean instead of dumping aggregator junk. Web finds land in the same tracker,
tagged `ats: web`.

---

## Install

### Prerequisites
- **Python 3.11+**
- **Claude Desktop** (this runs as an MCP server it launches)
- An **Anthropic API key** (only needed for `setup_profile` and `tailor_resume`)
- *Optional:* a free **Adzuna API key** (`app_id` + `app_key` from
  [developer.adzuna.com](https://developer.adzuna.com)) — only for `search_web_jobs`

### 1. Clone
```bash
git clone <your-repo-url> jarvis-job-agent
cd jarvis-job-agent
```

### 2. Create a venv and install deps
```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements.txt
# sanity check:
./.venv/bin/python -c "import mcp, yaml, requests, docx; print('deps ok')"
```

### 3. Register it with Claude Desktop
Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) and add
a server. Point `command` at the venv's Python and `args` at `jarvis_mcp.py`:

```json
{
  "mcpServers": {
    "jarvis": {
      "command": "/absolute/path/to/jarvis-job-agent/.venv/bin/python",
      "args": ["/absolute/path/to/jarvis-job-agent/jarvis_mcp.py"],
      "env": {
        "ANTHROPIC_API_KEY": "sk-ant-...",
        "ADZUNA_APP_ID": "...",
        "ADZUNA_APP_KEY": "..."
      }
    }
  }
}
```
Restart Claude Desktop.

### 4. Drop in your résumé and build your profile
```bash
cp /path/to/your_resume.docx profile/resume_base.docx   # exact filename
```
In Claude Desktop, run **`setup_profile`** (e.g. say *"set up my profile"*). It reads the
résumé, derives your target role + title keywords, and writes `profile.yaml`.

### 5. Search
Say **"wake up Jarvis."** You'll get today's digest and the jobs land in `tracker.csv`.
Then use `mark_applied`, `tailor_resume`, etc.

---

## Customizing the company list

`sponsors.yaml` is the registry of companies Jarvis searches. Each entry resolves to one
ATS feed:

```yaml
companies:
  - {name: Stripe,    ats: greenhouse, token: stripe}
  - {name: Netflix,   ats: lever,      token: netflix}
  - {name: Snowflake, ats: ashby,      token: snowflake}
  - {name: Medtronic, ats: workday, tenant: medtronic, host: wd1, site: medtronicCareers}
```

- **Greenhouse / Lever / Ashby** need just a `token` (the board slug). Verify it by opening
  `boards.greenhouse.io/<token>`, `jobs.lever.co/<token>`, or `jobs.ashbyhq.com/<token>`.
- **Workday** needs `tenant`, `host` (the `wdN` shard), and `site` — read them out of the
  careers URL: `https://<tenant>.<host>.myworkdayjobs.com/<site>`.
- A bad slug **fails loudly** in the digest's "Failed sources" line; it never silently
  empties the run. One broken company never aborts the others.

> **Note:** Many large employers (Google, Meta, Netflix-corp, most Indian IT
> consultancies, ServiceNow, etc.) run proprietary or non-public ATS portals and
> **cannot** be sourced this way.

---

## Daily automation (optional)

`run_daily.sh` runs the exact same pipeline as `find_jobs` on a schedule. Point cron or a
launchd agent at it:
```bash
0 8 * * *  /absolute/path/to/jarvis-job-agent/run_daily.sh
```
(Put `ANTHROPIC_API_KEY=...` in a local `.env` if the scheduled run needs `tailor_resume`.)

---

## Data & privacy

Everything personal stays **local and git-ignored**: your résumé (`profile/`), derived
filter (`profile.yaml`), tracked jobs (`tracker.csv`), generated résumés (`resumes/`),
digests (`jobs/`), and `.env`. Only code + the company registry + examples are committed.

---

## Repo layout

| File | Role |
|------|------|
| `jarvis_mcp.py` | MCP server: tools, tracker I/O, résumé profiling & tailoring |
| `jarvis_sourcing.py` | Sourcing engine: ATS adapters, the 7 gates, profile loader |
| `sponsors.yaml` | The curated company → ATS registry |
| `profile.example.yaml` | Shape of the generated `profile.yaml` |
| `apply.py` | Standalone CLI to view/mark the tracker without Claude |
| `job_agent.py` / `run_daily.sh` | Cron entrypoint (same pipeline as `find_jobs`) |
| `requirements.txt` | `mcp`, `requests`, `pyyaml`, `python-docx` |

---

*Built as a Claude Desktop MCP connector. Résumé tailoring uses the Anthropic API
(`claude-sonnet-4-5`) and re-emphasizes only real experience — it never fabricates.*
