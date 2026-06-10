"""Subsystem #6 — the project steward: Projects v2 placement, status, priority.

Closes the loop from advising to filing: places matched issues on a board, syncs
Status from linked PRs only, and surfaces the organizer's ranking — never fighting a
human. The decision core (`decide`) and the per-field human veto (`veto`) are pure /
kv-only and carry the invariants; `board` is the GraphQL I/O, `run` the orchestrator.
"""
