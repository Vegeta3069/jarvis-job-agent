#!/usr/bin/env python3
"""Cron entrypoint. Runs the same combined pipeline as the MCP daily_jobs tool
(sponsor list first, then web), so the scheduled run and 'wake up Jarvis'
produce the same daily list of up to 30 jobs."""
import jarvis_mcp
print(jarvis_mcp.daily_jobs())
