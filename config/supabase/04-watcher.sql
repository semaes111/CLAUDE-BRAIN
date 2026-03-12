-- 04-watcher.sql
-- Tabla de audit trail del Watcher (todas las interacciones)
-- y tabla mem0_memories para la memoria semántica

-- ── Tabla interactions ──────────────────────────────────────
CREATE TABLE IF NOT EXISTS interactions (
    id                  TEXT PRIMARY KEY,
    session_id          TEXT NOT NULL,
    task_preview        TEXT,
    task_hash           TEXT,
    agent_used          TEXT,
    skills_used         JSONB DEFAULT '[]'::jsonb,
    command_used        TEXT,
    routing_reasoning   TEXT,
    routing_confidence  FLOAT DEFAULT 0.0,
    response_preview    TEXT,
    success             BOOLEAN DEFAULT true,
    error               TEXT,
    latency_ms          INTEGER DEFAULT 0,
    tokens_estimated    INTEGER DEFAULT 0,
    created_at          TIMESTAMPTZ DEFAULT now()
);

-- Índices para análisis
CREATE INDEX IF NOT EXISTS idx_interactions_session
    ON interactions (session_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_interactions_agent
    ON interactions (agent_used, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_interactions_success
    ON interactions (success, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_interactions_hash
    ON interactions (task_hash);

-- Vista de métricas por agente
CREATE OR REPLACE VIEW agent_metrics AS
SELECT
    agent_used,
    COUNT(*)                           AS total_uses,
    ROUND(AVG(latency_ms))             AS avg_latency_ms,
    ROUND(AVG(tokens_estimated))       AS avg_tokens,
    SUM(CASE WHEN success THEN 1 END)  AS successes,
    SUM(CASE WHEN NOT success THEN 1 END) AS failures,
    ROUND(
        SUM(CASE WHEN success THEN 1 END)::numeric / COUNT(*) * 100, 1
    ) AS success_rate_pct
FROM interactions
WHERE agent_used IS NOT NULL
GROUP BY agent_used
ORDER BY total_uses DESC;

-- Vista de actividad por sesión
CREATE OR REPLACE VIEW session_activity AS
SELECT
    session_id,
    COUNT(*)          AS total_requests,
    MIN(created_at)   AS first_seen,
    MAX(created_at)   AS last_seen,
    ROUND(AVG(latency_ms)) AS avg_latency_ms,
    SUM(tokens_estimated)  AS total_tokens
FROM interactions
GROUP BY session_id
ORDER BY last_seen DESC;

-- ── Tabla mem0_memories (creada por mem0ai automáticamente) ──
-- mem0 crea su propia tabla, pero la creamos aquí para asegurar
-- que pgvector esté habilitado antes de que mem0 intente usarla

CREATE TABLE IF NOT EXISTS mem0_memories (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT NOT NULL,
    agent_id    TEXT,
    run_id      TEXT,
    memory      TEXT NOT NULL,
    embedding   extensions.vector(768),
    metadata    JSONB DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_mem0_user
    ON mem0_memories (user_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_mem0_hnsw
    ON mem0_memories
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- ─────────────────────────────────────────────────────────
-- Columnas epistémicas en la tabla interactions
-- ─────────────────────────────────────────────────────────
ALTER TABLE interactions
  ADD COLUMN IF NOT EXISTS degradation_score     FLOAT   DEFAULT 0.0,
  ADD COLUMN IF NOT EXISTS degradation_triggers  JSONB   DEFAULT '[]';

-- Índice para consultar sesiones con alta degradación
CREATE INDEX IF NOT EXISTS idx_interactions_degradation
  ON interactions (degradation_score DESC)
  WHERE degradation_score > 0.3;

-- Vista: sesiones con problemas epistémicos
CREATE OR REPLACE VIEW degradation_report AS
SELECT
  session_id,
  COUNT(*)                                           AS total_interactions,
  ROUND(AVG(degradation_score)::numeric, 3)          AS avg_degradation,
  MAX(degradation_score)                             AS max_degradation,
  COUNT(*) FILTER (WHERE degradation_score > 0.5)   AS severe_alerts,
  MAX(created_at)                                    AS last_activity
FROM interactions
WHERE degradation_score > 0
GROUP BY session_id
ORDER BY avg_degradation DESC;

