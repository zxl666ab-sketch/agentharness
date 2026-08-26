"""Versioned SQLite schema migrations."""

from __future__ import annotations

SCHEMA_VERSION = 12

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
    5: """
    CREATE TABLE IF NOT EXISTS run_leases (
        run_id TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        acquired_at TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        FOREIGN KEY (run_id) REFERENCES runs(id)
    );
    CREATE INDEX IF NOT EXISTS idx_run_leases_expires ON run_leases(expires_at);

    CREATE TABLE IF NOT EXISTS run_pins (
        run_id TEXT PRIMARY KEY,
        pinned_at TEXT NOT NULL,
        note TEXT,
        FOREIGN KEY (run_id) REFERENCES runs(id)
    );
    """,
    6: """
    ALTER TABLE memories ADD COLUMN content_hash TEXT NOT NULL DEFAULT '';
    ALTER TABLE memories ADD COLUMN updated_at TEXT;
    ALTER TABLE memories ADD COLUMN expires_at TEXT;
    ALTER TABLE memories ADD COLUMN use_count INTEGER NOT NULL DEFAULT 0;
    CREATE INDEX IF NOT EXISTS idx_memories_scope_hash
        ON memories(scope, content_hash);
    CREATE INDEX IF NOT EXISTS idx_memories_scope_expiry
        ON memories(scope, expires_at);
    """,
    7: """
    ALTER TABLE approvals
        ADD COLUMN requires_confirmation INTEGER NOT NULL DEFAULT 0;
    """,
    8: """
    ALTER TABLE messages ADD COLUMN tool_result_json TEXT;
    ALTER TABLE approvals ADD COLUMN invocation_id TEXT;
    ALTER TABLE approvals ADD COLUMN tool_version TEXT NOT NULL DEFAULT '1';
    ALTER TABLE approvals ADD COLUMN arguments_sha256 TEXT NOT NULL DEFAULT '';
    ALTER TABLE approvals ADD COLUMN approval_scope TEXT NOT NULL DEFAULT '';
    ALTER TABLE approvals ADD COLUMN status TEXT NOT NULL DEFAULT 'pending';
    UPDATE approvals SET status = 'resolved' WHERE decision IS NOT NULL;

    CREATE TABLE IF NOT EXISTS tool_invocations (
        id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        step INTEGER NOT NULL,
        ordinal INTEGER NOT NULL,
        provider_call_id TEXT NOT NULL,
        tool_name TEXT NOT NULL,
        tool_version TEXT NOT NULL DEFAULT '1',
        status TEXT NOT NULL,
        effect TEXT NOT NULL,
        replay_policy TEXT NOT NULL,
        arguments_json TEXT NOT NULL DEFAULT '{}',
        arguments_sha256 TEXT NOT NULL,
        approval_id TEXT,
        attempt_count INTEGER NOT NULL DEFAULT 0,
        result_json TEXT,
        error_code TEXT,
        error_category TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        UNIQUE(run_id, step, ordinal),
        FOREIGN KEY (run_id) REFERENCES runs(id)
    );
    CREATE INDEX IF NOT EXISTS idx_tool_invocations_run
        ON tool_invocations(run_id, step, ordinal);
    CREATE INDEX IF NOT EXISTS idx_tool_invocations_status
        ON tool_invocations(status, updated_at);

    CREATE TABLE IF NOT EXISTS tool_attempts (
        id TEXT PRIMARY KEY,
        invocation_id TEXT NOT NULL,
        attempt INTEGER NOT NULL,
        status TEXT NOT NULL,
        error_code TEXT,
        error_category TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        duration_ms REAL,
        FOREIGN KEY (invocation_id) REFERENCES tool_invocations(id),
        UNIQUE(invocation_id, attempt)
    );
    CREATE INDEX IF NOT EXISTS idx_tool_attempts_invocation
        ON tool_attempts(invocation_id, attempt);
    """,
    9: """
    CREATE TABLE IF NOT EXISTS procurement_requests (
        id TEXT PRIMARY KEY,
        reference TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        category TEXT NOT NULL,
        item_name TEXT NOT NULL,
        quantity INTEGER NOT NULL,
        unit TEXT NOT NULL,
        specifications_json TEXT NOT NULL DEFAULT '{}',
        constraints_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL,
        session_id TEXT NOT NULL,
        analysis_run_id TEXT,
        current_snapshot_id TEXT,
        approved_quote_id TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (session_id) REFERENCES sessions(id),
        FOREIGN KEY (analysis_run_id) REFERENCES runs(id)
    );
    CREATE INDEX IF NOT EXISTS idx_procurement_requests_updated
        ON procurement_requests(updated_at DESC);

    CREATE TABLE IF NOT EXISTS procurement_quotes (
        id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        supplier_name TEXT NOT NULL,
        source_filename TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        source_artifact_id TEXT NOT NULL,
        source_sha256 TEXT NOT NULL,
        extracted_json TEXT NOT NULL,
        status TEXT NOT NULL,
        review_count INTEGER NOT NULL DEFAULT 0,
        parser_version TEXT NOT NULL,
        processing_ms REAL NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        FOREIGN KEY (request_id) REFERENCES procurement_requests(id),
        FOREIGN KEY (source_artifact_id) REFERENCES artifacts(id)
    );
    CREATE INDEX IF NOT EXISTS idx_procurement_quotes_request
        ON procurement_quotes(request_id, created_at);

    CREATE TABLE IF NOT EXISTS procurement_comparison_snapshots (
        id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        version INTEGER NOT NULL,
        input_sha256 TEXT NOT NULL,
        result_json TEXT NOT NULL,
        artifact_id TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(request_id, version),
        FOREIGN KEY (request_id) REFERENCES procurement_requests(id),
        FOREIGN KEY (run_id) REFERENCES runs(id),
        FOREIGN KEY (artifact_id) REFERENCES artifacts(id)
    );
    CREATE INDEX IF NOT EXISTS idx_procurement_snapshots_request
        ON procurement_comparison_snapshots(request_id, version DESC);

    CREATE TABLE IF NOT EXISTS procurement_decisions (
        id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        snapshot_id TEXT NOT NULL,
        quote_id TEXT NOT NULL,
        run_id TEXT NOT NULL,
        approval_id TEXT NOT NULL,
        decision TEXT NOT NULL,
        note TEXT,
        actor TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(request_id),
        FOREIGN KEY (request_id) REFERENCES procurement_requests(id),
        FOREIGN KEY (snapshot_id) REFERENCES procurement_comparison_snapshots(id),
        FOREIGN KEY (quote_id) REFERENCES procurement_quotes(id),
        FOREIGN KEY (run_id) REFERENCES runs(id)
    );

    CREATE TABLE IF NOT EXISTS procurement_audit_events (
        id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        quote_id TEXT,
        run_id TEXT,
        type TEXT NOT NULL,
        actor TEXT NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        FOREIGN KEY (request_id) REFERENCES procurement_requests(id),
        FOREIGN KEY (quote_id) REFERENCES procurement_quotes(id),
        FOREIGN KEY (run_id) REFERENCES runs(id)
    );
    CREATE INDEX IF NOT EXISTS idx_procurement_audit_request
        ON procurement_audit_events(request_id, created_at);
    """,
    10: """
    -- A procurement task may finish without selecting a supplier.  Rebuild
    -- the decision table because SQLite cannot relax the existing NOT NULL
    -- quote_id constraint in place.
    CREATE TABLE procurement_decisions_v10 (
        id TEXT PRIMARY KEY,
        request_id TEXT NOT NULL,
        snapshot_id TEXT NOT NULL,
        quote_id TEXT,
        run_id TEXT NOT NULL,
        approval_id TEXT NOT NULL,
        decision TEXT NOT NULL CHECK (decision IN ('approved', 'no_award')),
        note TEXT,
        actor TEXT NOT NULL,
        created_at TEXT NOT NULL,
        UNIQUE(request_id),
        FOREIGN KEY (request_id) REFERENCES procurement_requests(id),
        FOREIGN KEY (snapshot_id) REFERENCES procurement_comparison_snapshots(id),
        FOREIGN KEY (quote_id) REFERENCES procurement_quotes(id),
        FOREIGN KEY (run_id) REFERENCES runs(id)
    );
    INSERT INTO procurement_decisions_v10(
        id, request_id, snapshot_id, quote_id, run_id, approval_id,
        decision, note, actor, created_at
    )
    SELECT id, request_id, snapshot_id, quote_id, run_id, approval_id,
           decision, note, actor, created_at
    FROM procurement_decisions;
    DROP TABLE procurement_decisions;
    ALTER TABLE procurement_decisions_v10 RENAME TO procurement_decisions;

    ALTER TABLE procurement_requests
        ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1;
    ALTER TABLE procurement_requests
        ADD COLUMN quantity_decimal TEXT;
    """,
    11: """
    CREATE TABLE IF NOT EXISTS internal_operations (
        operation_id TEXT PRIMARY KEY,
        payload_sha256 TEXT NOT NULL,
        operation_type TEXT NOT NULL,
        aggregate_id TEXT NOT NULL,
        status TEXT NOT NULL,
        result_json TEXT,
        error TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_internal_operations_status
        ON internal_operations(status, updated_at);
    """,
    12: """
    ALTER TABLE internal_operations ADD COLUMN run_id TEXT;
    CREATE INDEX IF NOT EXISTS idx_internal_operations_run
        ON internal_operations(run_id);
    """,
}


def apply_migrations(conn) -> int:
    """Apply pending migrations. `conn` is a sqlite3 connection."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    conn.commit()
    row = conn.execute(
        "SELECT value FROM schema_meta WHERE key = 'schema_version'"
    ).fetchone()
    current = int(row[0]) if row else 0
    for version in sorted(MIGRATIONS.keys()):
        if version <= current:
            continue
        script = f"""
        BEGIN IMMEDIATE;
        {MIGRATIONS[version]}
        INSERT INTO schema_meta(key, value)
        VALUES('schema_version', '{version}')
        ON CONFLICT(key) DO UPDATE SET value = excluded.value;
        COMMIT;
        """
        try:
            conn.executescript(script)
        except Exception:
            if conn.in_transaction:
                conn.rollback()
            raise
        current = version
    return current
