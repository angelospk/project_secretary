# OpenCouncil: πιθανές προεκτάσεις & εντοπισμένα προβλήματα

Ημερομηνία: 2026-07-06
Αφορμή: η παρουσίαση του project secretary στην ομάδα του OpenCouncil
(live: https://opencouncil-secretary-pitch.vercel.app · source: `~/projects/presentations/opencouncil-secretary-pitch`).

Δύο μέρη: (Α) πού μπορεί να εξελιχθεί το project για το OpenCouncil, (Β) προβλήματα
και εύθραυστα σημεία που εντοπίστηκαν σε σάρωση του κώδικα (2026-07-06, με
δειγματοληπτική επαλήθευση των σημαντικότερων). Τα (Β) είναι υλικό για μελλοντικά
issues, όχι υποχρεωτικά fixes τώρα.

---

## Α. Πιθανές προεκτάσεις (με σειρά ρεαλισμού, όχι εντυπωσιασμού)

Κοινό νήμα: όλες πατούν στη μνήμη που ήδη υπάρχει (γράφος + vectors + reranker).
Καμία δεν απαιτεί νέο store ή νέα φιλοσοφία εγγραφών (report → suggest → act).

### 1. Ζωντανό roadmap ανά scope/release — το πιο κοντινό
Τα δεδομένα υπάρχουν ήδη (milestones, Projects v2 items, labels, `secretary plan`).
Λείπει μόνο μια προβολή: ομαδοποίηση ανά κατηγορία/scope με focus filter για το
επόμενο release. Φυσική θέση: επέκταση του web console (#11). Χαμηλό ρίσκο,
καθαρά read-path.

### 2. PR review triage — μικρό βήμα από ό,τι υπάρχει
Ο reranker και ο labeler δουλεύουν ήδη πάνω σε PRs ως αντικείμενα μνήμης.
Η επέκταση είναι: ταξινόμηση ανοιχτών PRs (μέγεθος, ρίσκο περιοχής, σχέση με
milestone) και σειρά προτεραιότητας για review. Πρόσοχή: η «προτεραιότητα» να
μείνει advisory (organizer-style), αλλιώς συγκρούεται με την κρίση του maintainer.

### 3. Taskboard με συνθέσιμα widgets
Η ιδέα: η κάθε προβολή (bug hunt, sprint, θεματική περιοχή) συντίθεται από widgets
πάνω στα ίδια ερωτήματα μνήμης. Προαπαιτεί το console να αποκτήσει ένα ελαφρύ
widget/layout layer. Μέτριο effort, αλλά προσοχή στο scope creep: το 2026-07 pivot
μακριά από το NL dashboard έγινε ακριβώς επειδή τέτοια UI γίνονται «shiny trap».
Προτεινόμενο όριο: προκαθορισμένα widgets με config, όχι αυθαίρετη NL σύνθεση.

### 4. Απομαγνητοφώνηση συνεδρίασης → tasks/issues
Νέο κανάλι εισόδου (audio pipeline: transcription + diarization) που καταλήγει στο
ήδη υπάρχον triage path (ingest → embed → related → enrich). Το δύσκολο δεν είναι
το STT· είναι (α) ο εντοπισμός actionable αποσπασμάτων, (β) το mapping σε υπάρχοντα
issues αντί για δημιουργία διπλών — εδώ βοηθά ο reranker, (γ) το trust model:
πάντα draft issues προς έγκριση, ποτέ αυτόματο άνοιγμα. Για το OpenCouncil υπάρχει
ήδη σχετική υποδομή transcription στο οικοσύστημα — να εξεταστεί επαναχρησιμοποίηση
πριν χτιστεί κάτι νέο.

### 5. Ζωντανός βοηθός στη συζήτηση — πειραματικό, τελευταίο
Real-time retrieval πάνω στη ροή της συζήτησης. Τεχνικά: streaming transcription +
συνεχή ερωτήματα στο `ask` path. Τα δύσκολα: latency, το πότε «παρεμβαίνει», και
ότι εύκολα υπόσχεται περισσότερα απ' όσα δίνει. Να μπει μόνο ως demo/experiment
αφού δουλέψουν τα 1-4. (Στην παρουσίαση σημειώθηκε ήδη ως «πειραματικό».)

### Εκτός λίστας, αλλά κοντά
- **Digest ως ανάρτηση/ενημέρωση για δημότες**: το reporter path (#10) με άλλο
  template και άλλο κοινό. Φθηνό, αξιοποιεί τα ίδια δεδομένα.
- **Cross-repo με τα repos του OpenCouncil οικοσυστήματος**: ήδη υποστηρίζεται
  (`SECRETARY_GITHUB_REPOS`), απλώς θέλει configuration και backfill.

---

## Β. Εντοπισμένα προβλήματα / εύθραυστα σημεία

Από σάρωση του κώδικα (Explore pass 2026-07-06). Τα «Υψηλά» επαληθεύτηκαν
δειγματοληπτικά με grep στα αναφερόμενα σημεία.

### Υψηλής προτεραιότητας

1. **Fallback session secret στο console** — `console/app.py` (~:179)
   Το SessionMiddleware πέφτει σε σταθερό `"insecure-dev-only"` όταν λείπει το
   `console_session_secret`. Υπάρχει startup guard όταν έχει οριστεί password,
   αλλά το fallback αφήνει παράθυρο για forgeable cookies σε misconfigured
   deployment. Πρόταση: fail-fast ή τυχαίο ephemeral secret αντί για σταθερό.

2. **Καμία επανασύνδεση/retry στο SurrealDB** — `db/connection.py`
   Κάθε worker ανοίγει fresh connection· σε στιγμιαία πτώση της βάσης τα in-flight
   webhook events αποτυγχάνουν και δεν ξαναμπαίνουν σε ουρά (τα μαζεύει το επόμενο
   reconcile, με καθυστέρηση). Πρόταση: μικρό retry με backoff στο connection
   context manager.

3. **DeepWiki: hardcoded endpoints χωρίς ειδοποίηση αποτυχίας** — `deepwiki/client.py:20-21`
   Reverse-engineered API (`api.devin.ai`) χωρίς SLA· σε μόνιμη αλλαγή του endpoint
   το enrichment degrade-άρει σιωπηλά σε κενό. Πρόταση: μετρικό/log-based alert όταν
   το ποσοστό αποτυχιών DeepWiki μείνει 100% για N ώρες.

4. **Embedding dimension hardcoded (384) χωρίς runtime guard** — `embeddings/embedder.py:17`, `db/schema.surql`
   Αλλαγή μοντέλου σε άλλη διάσταση = σιωπηλή διαφθορά του HNSW index. Το roadmap
   ήδη το σημειώνει ως footgun· λείπει fail-fast έλεγχος `embedder.dim == 384` στο
   startup και τεκμηριωμένη διαδικασία re-embed. (Σχετίζεται με το μελλοντικό
   `SECRETARY_EMBEDDING_MODEL`.)

5. **Ασθενής έλεγχος του scrypt hash στο config** — `config.py` (~:234-246)
   Πιάνει μόνο το «ξέχασα να κάνω hash» (δεν ξεκινά με `scrypt$`)· ένα κομμένο ή
   κακοσχηματισμένο hash περνάει το validation και το login αποτυγχάνει σιωπηλά στο
   runtime. Πρόταση: parse/verify του hash format στο config load.

### Μεσαίας προτεραιότητας

6. **GitHub rate-limit backoff**: 5 retries με hardcoded 60s fallback
   (`github/client.py`)· σε παρατεταμένο φορτίο ένα reconcile batch αποτυγχάνει.
7. **Queue overflow στο serve**: ουρά 64 θέσεων → 503 (το καλύπτει GitHub retry +
   reconcile, τεκμηριωμένο σχεδιαστικά), αλλά δεν υπάρχει operator signal όταν
   συμβαίνει συστηματικά.
8. **Γενικόλογο logging στο worker pool**: DB error, κακό JSON και GitHub failure
   φαίνονται ίδια («triage task failed»)· δυσκολεύει το debugging config λαθών.
9. **GraphQL errors χωρίς κατηγοριοποίηση**: auth, syntax και rate-limit
   ανεβαίνουν όλα ως ίδιο RuntimeError.
10. **`taxonomy_path` που λείπει απενεργοποιεί σιωπηλά τον labeler**: ούτε warning
    στο startup. Πρόταση: έλεγχος ύπαρξης αρχείου στο config load.
11. **Χωρίς έλεγχο για κενό `github_token`**: πέφτει σε unauthenticated 60 req/h
    και σκάει στο πρώτο poll, χωρίς σαφές μήνυμα αιτίας.
12. **Judge offline = σιωπηλή αποχή**: αν το LLM API είναι κάτω για ώρες, όλα
    abstain χωρίς ένδειξη ότι ο judge είναι νεκρός.
13. **DeepWiki timeout 120s (default)**: μπορεί να μπλοκάρει workers σε burst·
    ρυθμιζόμενο, αλλά ψηλό default για webhook path.

### Σωστά φτιαγμένα (για ισορροπία)

- HMAC verification με `hmac.compare_digest`, σωστός χειρισμός κενών υπογραφών.
- Session rotation στο login/logout (κατά session fixation).
- Forward-only migrations με ledger και fail-fast σε νεότερη DB version.

### Προτεινόμενη σειρά για fixes (όταν έρθει η ώρα)

1 και 5 (console security, φθηνά και σοβαρά) → 4 (dim guard, μία γραμμή fail-fast)
→ 2 (DB retry) → 10, 11 (config validation, quality-of-life) → τα υπόλοιπα κατά
περίπτωση, ιδανικά μαζί με τα subsystems που αγγίζουν.
