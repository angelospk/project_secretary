# Πού είμαστε, τι δουλεύει, τι λείπει

Γραμμένο 7 Σεπτεμβρίου 2026, για να μπορεί μια καινούρια συνομιλία να πιάσει το
project από το μηδέν. Επαληθευμένο με ανάγνωση κώδικα και εκτέλεση του suite την
ίδια μέρα, όχι από μνήμη.

## Σε μία παράγραφο

Το `project_secretary` κρατά μνήμη ενός GitHub backlog: κάνει ingest issues, PRs,
σχόλια, cross-references και Project v2 items σε SurrealDB (γράφος + vectors),
βρίσκει σχετικά με embeddings, και **αποφασίζει με reranker** αν κάτι είναι
διπλότυπο, επικάλυψη, χρήσιμο ιστορικό ή θόρυβος. Μετά γράφει πίσω ένα sticky
σχόλιο με το σχετικό ιστορικό και context από το DeepWiki.

**Είναι λειτουργικό.** 361 tests πράσινα, 24 skipped. Δεν είναι εγκαταλειμμένο —
ήταν σταματημένο.

## Τι δουλεύει σήμερα

Επαληθεύτηκε ότι υπάρχει και περνάει tests:

- `backfill` / `embed` / `related` / `enrich` — η κύρια ροή, από ingest μέχρι
  γραμμένο σχόλιο.
- `ask` — semantic search πάνω στη μνήμη· με `SECRETARY_QA_MODEL` γράφει
  απάντηση με παραπομπές, χωρίς αυτό τυπώνει τα raw hits.
- `mcp` — read-only MCP server, ώστε ένας agent (π.χ. Claude Code) να ρωτάει τη
  μνήμη χωρίς να μπορεί να τη γράψει.
- `garden` / `digest` / `notes` — προτάσεις για stale issues, εβδομαδιαίο digest,
  προσχέδιο release notes. Όλα dry-run by default.
- `console` — read-mostly web console με insights και admin.
- Cross-repo: composite ids, οπότε το issue #42 σε δύο repos δεν συγκρούεται.

**Προσγειώθηκαν σήμερα** (5 branches που περίμεναν από 30 Ιουνίου):
taxonomy cold-start από υπάρχοντα labels · εξήγηση των label suggestions με
runner-up και margin · `gardener_findings` ως MCP read tool · ρυθμιζόμενο
embedding model με fail-fast dimension guard · review queues στο console.

## Τι είναι παρκαρισμένο επίτηδες

- `feat/nl-dashboard-widgets` (+989 γραμμές, WIP). Το brief της 22ας Ιουλίου το
  σημειώνει ρητά ως «μην το αγγίξεις». Το pivot μακριά από NL dashboard έγινε
  γιατί τέτοια UI είναι «shiny trap». **Μην το προσγειώσεις χωρίς να το ξανα-
  αποφασίσεις.**

## Τα κενά, με σειρά

Οι αριθμοί δείχνουν στο `docs/2026-07-06-opencouncil-extensions-and-risks.md`
μέρος Β, όπου υπάρχει η πλήρης αιτιολόγηση.

### Θα το διόρθωνα πρώτο

1. **Το console πέφτει σε σταθερό session secret** (`console/app.py:191`,
   `"insecure-dev-only"`). Υπάρχει startup guard όταν έχει οριστεί password,
   αλλά σε misconfigured deployment τα cookies γίνονται forgeable. Φθηνό: ή
   fail-fast, ή τυχαίο ephemeral secret. **Επιβεβαιώθηκε ανοιχτό σήμερα.**
2. **Το scrypt hash δεν επικυρώνεται σωστά** (`config.py`). Πιάνει μόνο το
   «ξέχασα να κάνω hash»· ένα κομμένο hash περνάει και το login αποτυγχάνει
   σιωπηλά στο runtime. **Ανοιχτό.**
3. **Καμία επανασύνδεση στο SurrealDB** (`db/connection.py`). Σε στιγμιαία πτώση
   της βάσης τα in-flight webhook events χάνονται μέχρι το επόμενο reconcile.
   Μικρό retry με backoff στο connection context manager. **Ανοιχτό.**

### Μετά

4. **Config που αποτυγχάνει σιωπηλά.** `taxonomy_path` που λείπει απενεργοποιεί
   τον labeler χωρίς warning· κενό `github_token` πέφτει σε 60 req/h και σκάει
   στο πρώτο poll χωρίς σαφές μήνυμα. Και τα δύο είναι έλεγχοι στο config load.
5. **Το DeepWiki είναι reverse-engineered endpoint χωρίς SLA**
   (`deepwiki/client.py`). Αν αλλάξει, το enrichment degrade-άρει **σιωπηλά** σε
   κενό. Θέλει alert όταν το ποσοστό αποτυχιών μείνει 100% για N ώρες.
6. **Ο judge offline μοιάζει με ομόφωνη αποχή.** Αν το LLM API είναι κάτω,
   όλα abstain χωρίς ένδειξη ότι ο judge είναι νεκρός.
7. **Γενικόλογο logging στο worker pool.** DB error, κακό JSON και GitHub failure
   φαίνονται ίδια («triage task failed»).

### Το dimension guard έκλεισε

Το #4 της παλιάς λίστας (hardcoded 384 χωρίς runtime έλεγχο) καλύφθηκε από το
`feat/configurable-embedding-model` που μπήκε σήμερα.

## Τι θα έχτιζα μετά, αν το project ξαναπιάσει

Με σειρά ρεαλισμού, από τη λίστα προεκτάσεων:

1. **Ζωντανό roadmap ανά scope/release.** Τα δεδομένα υπάρχουν ήδη — milestones,
   Projects v2 items, labels. Λείπει μόνο μια προβολή στο console. Καθαρά
   read-path, χαμηλό ρίσκο.
2. **PR review triage.** Ο reranker δουλεύει ήδη πάνω σε PRs. Η επέκταση είναι
   ταξινόμηση ανοιχτών PRs κατά μέγεθος, ρίσκο περιοχής και σχέση με milestone.
   Να μείνει **advisory** — αλλιώς συγκρούεται με την κρίση του maintainer.
3. **Cross-repo με τα repos του OpenCouncil.** Υποστηρίζεται ήδη
   (`SECRETARY_GITHUB_REPOS`)· θέλει configuration και backfill, όχι κώδικα.

## Πώς το τρέχεις

```bash
uv sync
surreal start --user root --pass root surrealkv://./.data/surreal.db &
uv run secretary init-db && uv run secretary backfill && uv run secretary embed
uv run secretary related 42        # τι σχετίζεται με το issue 42
uv run secretary enrich 42         # dry-run· --write --target comment για αληθινό
.venv/bin/python -m pytest -q      # 361 passed, 24 skipped
```
