# Night shift — 2026-07-22 · project_secretary

Αυτόνομο βραδινό task για agent(s). Self-contained: όλο το context είναι εδώ, δεν
χρειάζεται η συνομιλία που το παρήγαγε.

## Κατάσταση αφετηρίας (επαληθευμένη 2026-07-22)

- `main` @ `39554e2` — πράσινο: `uv run pytest -q` → **319 passed, 24 skipped**.
- 6 τοπικά feature branches από 2026-06-30/07-01, όλα 1 commit μπροστά από το main
  και **όλα κάνουν clean merge** (ελέγχθηκε με `git merge-tree --write-tree`):

  | Branch | Τι κάνει | Μέγεθος |
  |---|---|---|
  | `feat/labeler-taxonomy-bootstrap` | taxonomy-suggest cold-start από υπάρχοντα labels | +169 |
  | `feat/labeler-evidence` | εξήγηση label suggestions (runner-up + margin) | +70/−9 |
  | `feat/mcp-gardener-tool` | `gardener_findings` read tool στο MCP server | +54/−1 |
  | `feat/configurable-embedding-model` | configurable embedding model + fail-fast dimension guard | +148/−25 |
  | `feat/console-operational-queues` | review queues (duplicate-candidates, gardener-findings) στο console | +304/−2 |
  | `feat/nl-dashboard-widgets` | **ΠΑΡΚΑΡΙΣΜΕΝΟ (wip) — ΜΗΝ το αγγίξεις** | +989 |

- Γνωστά ρίσκα κώδικα: `docs/2026-07-06-opencouncil-extensions-and-risks.md`, μέρος Β.
  Επαληθεύτηκε σήμερα ότι τα υπ' αριθμόν 1, 5, 2, 10, 11 είναι **ακόμα ανοιχτά** στο main.

## Δουλειά της βραδιάς — δύο φάσεις, με αυτή τη σειρά

### Φάση 1 — Προσγείωση των 5 έτοιμων branches στο main

Για ΚΑΘΕ branch (με τη σειρά του πίνακα, εκτός του παρκαρισμένου nl-dashboard):

1. Διάβασε το diff (`git diff main...<branch>`) και κάνε ουσιαστικό review:
   correctness, edge cases, συνέπεια με το υπάρχον στυλ. Όχι rubber-stamp.
2. Αν το review είναι καθαρό: merge στο main (fast-forward όχι — κανονικό merge ή
   rebase+merge, διάλεξε ό,τι κρατά καθαρό ιστορικό) και τρέξε **όλο** το suite:
   `uv run pytest -q` και `uv run ruff check src/secretary`.
3. Αν βρεθεί πρόβλημα: διόρθωσέ το με μικρό fixup commit πάνω στο branch πριν το
   merge, ή —αν είναι σοβαρό/αμφίβολο— ΜΗΝ κάνεις merge· γράψε τι βρήκες στο
   τελικό report και προχώρα στο επόμενο branch.
4. Ειδικά για `feat/configurable-embedding-model`: το main πήρε στο `39554e2` fix
   στον embedder — βεβαιώσου ότι το branch δεν το αναιρεί (δες και τα δύο diffs
   στο `embeddings/embedder.py`). Αυτό το branch κλείνει και το risk #4 (dim
   guard) — σημείωσέ το στο report.
5. Μετά από κάθε επιτυχές merge: σβήσε το τοπικό branch (`git branch -d`).

Acceptance Φάσης 1: main πράσινο (tests + ruff) μετά από κάθε merge, όχι μόνο στο τέλος.

### Φάση 2 — Fixes στα ανοιχτά high-priority risks (doc 2026-07-06, μέρος Β)

Σειρά όπως προτείνει το doc. Για κάθε fix: **TDD** (test πρώτα, δες το να αποτυγχάνει,
μετά υλοποίηση), μικρό εστιασμένο commit.

1. **Risk #1 — console fallback session secret** (`src/secretary/console/app.py:179`)
   Το `settings.console_session_secret or "insecure-dev-only"` επιτρέπει forgeable
   cookies σε misconfigured deployment. Fix: όταν λείπει το secret, παρήγαγε τυχαίο
   ephemeral (`secrets.token_hex`) αντί για σταθερό — και fail-fast αν έχει οριστεί
   `console_password` χωρίς secret (δες τον υπάρχοντα startup guard δίπλα).
2. **Risk #5 — ασθενές scrypt validation** (`src/secretary/config.py:~237-246`)
   Τώρα ελέγχεται μόνο το prefix `scrypt$`. Fix: parse του πλήρους format στο config
   load (σωστός αριθμός πεδίων, αποκωδικοποιήσιμα n/r/p/salt/hash) ώστε κομμένο ή
   κακοσχηματισμένο hash να σκάει στο load, όχι σιωπηλά στο login.
3. **Risk #2 — SurrealDB χωρίς retry** (`src/secretary/db/connection.py`)
   Μικρό retry με backoff (π.χ. 3 προσπάθειες, 0.5s→2s) στο connection context
   manager, ώστε στιγμιαία πτώση της βάσης να μη χάνει in-flight webhook events.
   Κράτησέ το ελάχιστο — όχι νέο dependency, όχι circuit breaker.
4. **Risk #10 — σιωπηλή απενεργοποίηση labeler** (`src/secretary/config.py:182`)
   Αν `taxonomy_path` έχει τιμή αλλά το αρχείο δεν υπάρχει → σαφές error/warning στο
   config load.
5. **Risk #11 — κενό `github_token`** χωρίς σαφές μήνυμα: έλεγχος στο config load ή
   στο startup με actionable μήνυμα αιτίας.

Αν ο χρόνος/budget στενέψει, τα 1-2 είναι τα must· τα 3-5 nice-to-have.

## Guardrails

- **ΜΗΝ αγγίξεις** το `feat/nl-dashboard-widgets` — παρκαρισμένο σκόπιμα (pivot
  2026-07 σε small wins).
- Μόνο τοπικά commits — **κανένα push** στο origin· ο Harold θα κάνει review και
  push το πρωί.
- Το untracked `docs/2026-07-06-opencouncil-extensions-and-risks.md` και το
  `.claude/` μένουν ως έχουν (μην τα κάνεις commit εκτός αν ζητηθεί).
- Surgical changes: μόνο ό,τι περιγράφεται εδώ, τίποτα speculative.
- Verification: μετά από ΚΑΘΕ commit `uv run pytest -q` + `uv run ruff check src/secretary`.

## Τελικό report (γράψε το σε `docs/2026-07-22-night-shift-report.md`)

- Ποια branches μπήκαν / ποια όχι και γιατί.
- Ποια risks έκλεισαν, με ποια commits/tests.
- Ό,τι βρέθηκε στο review και αναβλήθηκε (υλικό για μελλοντικά issues).
- Τελική κατάσταση: αριθμός tests, ruff status, `git log --oneline` των νέων commits.
