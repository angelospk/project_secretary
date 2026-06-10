"""The organizer (subsystem #4): proactive, backlog-wide release planning.

Reads a GitHub Milestone's membership plus the graph/semantic layers already built,
and assembles one living "Release plan" issue: themes, dependency order, suggested
adds, coherence gaps, and a transparent priority ranking (structural metrics by
default, an optional configurable LLM judge layered on top).
"""
