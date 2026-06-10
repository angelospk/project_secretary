-- Memory backbone schema (subsystem #1).
-- Embedding fields are defined now but populated later (subsystem #2).

-- ---------------------------------------------------------------------------
-- Core record tables
-- ---------------------------------------------------------------------------

-- Record ids are composite arrays [repo, number] / [repo, gh_id], so the same
-- GitHub number in two repos never collides. The `repo` field mirrors the id's
-- first element for cheap filtering ("owner/name", lowercased).

DEFINE TABLE IF NOT EXISTS issue SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS repo       ON issue TYPE string;
DEFINE FIELD IF NOT EXISTS number     ON issue TYPE int;
DEFINE FIELD IF NOT EXISTS title      ON issue TYPE string;
DEFINE FIELD IF NOT EXISTS body       ON issue TYPE option<string>;
DEFINE FIELD IF NOT EXISTS state      ON issue TYPE string;
DEFINE FIELD IF NOT EXISTS author     ON issue TYPE option<string>;
DEFINE FIELD IF NOT EXISTS labels     ON issue TYPE array<string> DEFAULT [];
DEFINE FIELD IF NOT EXISTS url        ON issue TYPE option<string>;
DEFINE FIELD IF NOT EXISTS created_at ON issue TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS updated_at ON issue TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS closed_at  ON issue TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS milestone  ON issue TYPE option<string>;
DEFINE FIELD IF NOT EXISTS reactions      ON issue TYPE int DEFAULT 0;
DEFINE FIELD IF NOT EXISTS comments_count ON issue TYPE int DEFAULT 0;
DEFINE FIELD IF NOT EXISTS embedding  ON issue TYPE option<array<float>>;
DEFINE INDEX IF NOT EXISTS issue_embedding_hnsw ON issue FIELDS embedding HNSW DIMENSION 384 DIST COSINE;

DEFINE TABLE IF NOT EXISTS pr SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS repo          ON pr TYPE string;
DEFINE FIELD IF NOT EXISTS number        ON pr TYPE int;
DEFINE FIELD IF NOT EXISTS title         ON pr TYPE string;
DEFINE FIELD IF NOT EXISTS body          ON pr TYPE option<string>;
DEFINE FIELD IF NOT EXISTS state         ON pr TYPE string;
DEFINE FIELD IF NOT EXISTS author        ON pr TYPE option<string>;
DEFINE FIELD IF NOT EXISTS labels        ON pr TYPE array<string> DEFAULT [];
DEFINE FIELD IF NOT EXISTS url           ON pr TYPE option<string>;
DEFINE FIELD IF NOT EXISTS head_ref      ON pr TYPE option<string>;
DEFINE FIELD IF NOT EXISTS base_ref      ON pr TYPE option<string>;
DEFINE FIELD IF NOT EXISTS linked_issues ON pr TYPE array<int> DEFAULT [];
DEFINE FIELD IF NOT EXISTS reactions      ON pr TYPE int DEFAULT 0;
DEFINE FIELD IF NOT EXISTS comments_count ON pr TYPE int DEFAULT 0;
DEFINE FIELD IF NOT EXISTS created_at    ON pr TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS updated_at    ON pr TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS merged_at     ON pr TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS closed_at     ON pr TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS milestone     ON pr TYPE option<string>;
DEFINE FIELD IF NOT EXISTS embedding     ON pr TYPE option<array<float>>;
DEFINE INDEX IF NOT EXISTS pr_embedding_hnsw ON pr FIELDS embedding HNSW DIMENSION 384 DIST COSINE;

DEFINE TABLE IF NOT EXISTS comment SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS repo       ON comment TYPE string;
DEFINE FIELD IF NOT EXISTS gh_id      ON comment TYPE int;
DEFINE FIELD IF NOT EXISTS parent     ON comment TYPE record<issue | pr>;
DEFINE FIELD IF NOT EXISTS author     ON comment TYPE option<string>;
DEFINE FIELD IF NOT EXISTS body       ON comment TYPE option<string>;
DEFINE FIELD IF NOT EXISTS url        ON comment TYPE option<string>;
DEFINE FIELD IF NOT EXISTS created_at ON comment TYPE option<datetime>;
DEFINE FIELD IF NOT EXISTS updated_at ON comment TYPE option<datetime>;

DEFINE TABLE IF NOT EXISTS project_item SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS repo    ON project_item TYPE string;
DEFINE FIELD IF NOT EXISTS gh_id   ON project_item TYPE string;
DEFINE FIELD IF NOT EXISTS status  ON project_item TYPE option<string>;
DEFINE FIELD IF NOT EXISTS fields  ON project_item TYPE option<object> FLEXIBLE;
DEFINE FIELD IF NOT EXISTS content ON project_item TYPE option<record<issue | pr>>;

-- Sync watermark bookkeeping
DEFINE TABLE IF NOT EXISTS sync_state SCHEMAFULL;
DEFINE FIELD IF NOT EXISTS last_synced_at ON sync_state TYPE option<datetime>;

-- Organizer bookkeeping (subsystem #4): plan-issue numbers and LLM-judge cache.
-- Schemaless so it can hold either a scalar number or a {score, reason} object,
-- keyed `organizer_kv:[repo, key]`.
DEFINE TABLE IF NOT EXISTS organizer_kv SCHEMALESS;

-- ---------------------------------------------------------------------------
-- Graph relations (explicit GitHub cross-references; semantic links come in #2)
-- ---------------------------------------------------------------------------

DEFINE TABLE IF NOT EXISTS relates_to TYPE RELATION;
DEFINE TABLE IF NOT EXISTS mentions   TYPE RELATION;

-- Native GitHub edges (signal upgrade): typed blocked-by dependencies and sub-issue
-- links. depends_native carries the same ordering semantics as the body-parsed
-- depends_on; subissue_of is an annotation that never drives order.
DEFINE TABLE IF NOT EXISTS depends_native TYPE RELATION;
DEFINE TABLE IF NOT EXISTS subissue_of    TYPE RELATION;
