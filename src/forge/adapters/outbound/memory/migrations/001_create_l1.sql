CREATE TABLE IF NOT EXISTS episodes (
    episode_id TEXT PRIMARY KEY,
    schema_version INTEGER NOT NULL,
    session_id TEXT,
    request_id TEXT,
    created_at TEXT NOT NULL,
    task_request TEXT NOT NULL,
    task_category TEXT NOT NULL,
    execution_summary TEXT NOT NULL,
    execution_outcome TEXT NOT NULL,
    tool_names_json TEXT NOT NULL,
    result_quality REAL,
    requirement_coverage REAL,
    verification_confidence REAL,
    success_score REAL,
    pain_index REAL,
    retry_ratio REAL,
    tool_error_ratio REAL,
    budget_overrun_ratio REAL,
    rework_ratio REAL,
    human_intervention_ratio REAL,
    cib_score REAL,
    cib_evaluation_status TEXT NOT NULL,
    status TEXT NOT NULL,
    promotion_eligibility TEXT NOT NULL,
    retryable INTEGER NOT NULL,
    what_worked TEXT NOT NULL,
    what_failed TEXT NOT NULL,
    next_hint TEXT NOT NULL,
    causal_condition TEXT NOT NULL,
    has_reflection INTEGER NOT NULL,
    pattern_candidate_id TEXT,
    supersedes_episode_id TEXT UNIQUE REFERENCES episodes(episode_id),
    evidence_complete INTEGER NOT NULL,
    index_state TEXT NOT NULL,
    indexed_projection_version INTEGER,
    indexed_embedding_model_id TEXT,
    indexed_at TEXT,
    CHECK (execution_outcome IN ('completed', 'failed', 'halted')),
    CHECK (status IN ('Success', 'Partial', 'Failure')),
    CHECK (promotion_eligibility IN ('eligible', 'quarantined')),
    CHECK (cib_evaluation_status IN ('passed', 'failed', 'not_evaluated')),
    CHECK (index_state IN ('pending', 'indexed', 'stale', 'failed')),
    CHECK (retryable IN (0, 1)),
    CHECK (has_reflection IN (0, 1)),
    CHECK (evidence_complete IN (0, 1))
);
CREATE TABLE IF NOT EXISTS episode_event_refs (
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE RESTRICT,
    ordinal INTEGER NOT NULL,
    event_id TEXT NOT NULL,
    PRIMARY KEY (episode_id, ordinal),
    UNIQUE (episode_id, event_id)
);
CREATE TABLE IF NOT EXISTS episode_evaluation_reasons (
    episode_id TEXT NOT NULL REFERENCES episodes(episode_id) ON DELETE RESTRICT,
    metric TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    detail TEXT NOT NULL,
    PRIMARY KEY (episode_id, metric)
);
CREATE INDEX IF NOT EXISTS idx_episodes_created_at ON episodes(created_at);
CREATE INDEX IF NOT EXISTS idx_episodes_index_state ON episodes(index_state);
CREATE INDEX IF NOT EXISTS idx_episodes_supersedes ON episodes(supersedes_episode_id);
