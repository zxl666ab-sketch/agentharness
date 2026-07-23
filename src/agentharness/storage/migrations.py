"""Versioned SQLite schema migrations."""

from __future__ import annotations

SCHEMA_VERSION = 4

MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE IF NOT EXISTS schema_meta (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS sessions (
        id TEXT PRIMARY KEY,
        title TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        metadata_json TEXT DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS runs (
        id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        parent_run_id TEXT,
        root_run_id TEXT NOT NULL,
        status TEXT NOT NULL,
        provider TEXT,
        model TEXT,
        approval TEXT,
        cwd TEXT,
        error TEXT,
        output_summary TEXT,
        usage_json TEXT DEFAULT '{}',
        steps INTEGER DEFAULT 0,
        delegate_depth INTEGER DEFAULT 0,
        allow_write INTEGER DEFAULT 1,
        metadata_json TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        finished_at TEXT,
        FOREIGN KEY (session_id) REFERENCES sessions(id)
    );
    CREATE INDEX IF NOT EXISTS idx_runs_session ON runs(session_id);
    CREATE INDEX IF NOT EXISTS idx_runs_parent ON runs(parent_run_id);
    CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status);

    CREATE TABLE IF NOT EXISTS messages (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL,
        content TEXT,
        tool_call_id TEXT,
        name TEXT,
        tool_calls_json TEXT,
        seq INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES runs(id)
    );
    CREATE INDEX IF NOT EXISTS idx_messages_run ON messages(run_id);

    CREATE TABLE IF NOT EXISTS events (
        global_seq INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        run_seq INTEGER NOT NULL,
        session_id TEXT NOT NULL,
        root_run_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        parent_run_id TEXT,
        span_id TEXT,
        parent_span_id TEXT,
        type TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        schema_version INTEGER NOT NULL DEFAULT 1
    );
    CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, run_seq);
    CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);

    CREATE TABLE IF NOT EXISTS approvals (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        tool_call_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        effect TEXT NOT NULL,
        arguments_summary TEXT,
        decision TEXT,
        created_at TEXT NOT NULL,
        resolved_at TEXT,
        FOREIGN KEY (run_id) REFERENCES runs(id)
    );

    CREATE TABLE IF NOT EXISTS checkpoints (
        run_id TEXT PRIMARY KEY,
        phase TEXT NOT NULL,
        step INTEGER NOT NULL,
        data_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES runs(id)
    );

    CREATE TABLE IF NOT EXISTS memories (
        id TEXT PRIMARY KEY,
        content TEXT NOT NULL,
        source TEXT,
        scope TEXT DEFAULT 'global',
        created_at TEXT NOT NULL,
        last_used_at TEXT NOT NULL
    );
    CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
        content,
        source,
        scope,
        content='memories',
        content_rowid='rowid'
    );

    CREATE TABLE IF NOT EXISTS artifacts (
        id TEXT PRIMARY KEY,
        sha256 TEXT NOT NULL UNIQUE,
        content_type TEXT,
        size_bytes INTEGER,
        summary TEXT,
        path TEXT NOT NULL,
        created_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_artifacts_sha ON artifacts(sha256);
    """,
    2: """
    -- Session / message query indexes for multi-turn transcript & history load
    CREATE INDEX IF NOT EXISTS idx_sessions_updated ON sessions(updated_at DESC);
    CREATE INDEX IF NOT EXISTS idx_messages_session_seq ON messages(session_id, seq);
    CREATE INDEX IF NOT EXISTS idx_messages_session_run ON messages(session_id, run_id, seq);
    CREATE INDEX IF NOT EXISTS idx_runs_session_created ON runs(session_id, created_at);
    CREATE INDEX IF NOT EXISTS idx_runs_session_parent_status
        ON runs(session_id, parent_run_id, status, created_at);
    """,
    3: """
    CREATE TABLE IF NOT EXISTS stop_requests (
        run_id TEXT PRIMARY KEY,
        mode TEXT NOT NULL,
        requested_at TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES runs(id)
    );
    """,
    4: """
    -- Covering index for the run-scoped events read path
    -- (/api/runs/{id}/events, get_events(run_id=..., after_global_seq=...)).
    -- Lets the filter+ORDER BY global_seq run off one index with no temp B-tree.
    CREATE INDEX IF NOT EXISTS idx_events_run_global ON events(run_id, global_seq);
    """,
}


def apply_migrations(conn) -> int:
    """Apply pending migrations. `conn` is a sqlite3 connection."""
    conn.executescript(
        "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
    )
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    current = int(row[0]) if row else 0
    for version in sorted(MIGRATIONS.keys()):
        if version <= current:
            continue
        conn.executescript(MIGRATIONS[version])
        conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(version),),
        )
        conn.commit()
        current = version
    return current
