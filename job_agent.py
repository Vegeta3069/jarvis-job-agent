#!/usr/bin/env python3
"""Cron entrypoint. Runs the exact same pipeline as the MCP find_jobs tool,
so the 8 AM scheduled run and 'wake up Jarvis' produce identical results."""
import jarvis_mcp
print(jarvis_mcp.find_jobs())
