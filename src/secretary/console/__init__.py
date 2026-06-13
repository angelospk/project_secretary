"""The Web Console (subsystem #11): read-mostly insights + light management.

A standalone Starlette + Jinja + HTMX app (run by `secretary console`) over the stored
data. Viewer is public; admin is a single shared secret. All console-owned state lives in
`organizer_kv` under a `console.` namespace — the console never rewrites the human-owned
taxonomy or invents a new write philosophy.
"""
