-- 02-memories.sql
-- Tabla de memorias persistentes del agente
-- Embeddings: nomic-embed-text-v1.5 → 768 dimensiones

CREATE TABLE IF NOT EXISTS agent_memories (
    id          TEXT PRIMARY KEY,
    content     TEXT NOT NULL,
    memory_type TEXT NOT NULL CHECK (memory_type IN ('episodic', 'semantic', 'procedural')),
    embedding   extensions.vector(768),  -- nomic-embed-text-v1.5: 768 dims
    metadata    JSONB DEFAULT '{}'::jsonb,
    session_id  TEXT,
    created_at  TIMESTAMPTZ DEFAULT now(),
    updated_at  TIMESTAMPTZ DEFAULT now()
);

-- Índice HNSW para búsqueda aproximada por similitud coseno
-- m=16, ef_construction=64: balance calidad/velocidad para datasets medianos
CREATE INDEX IF NOT EXISTS idx_memories_hnsw
    ON agent_memories
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- Índice para filtrar por session_id
CREATE INDEX IF NOT EXISTS idx_memories_session
    ON agent_memories (session_id, created_at DESC);

-- Índice para filtrar por tipo
CREATE INDEX IF NOT EXISTS idx_memories_type
    ON agent_memories (memory_type);

-- Función de búsqueda semántica por similitud coseno
CREATE OR REPLACE FUNCTION match_memories(
    query_embedding extensions.vector(768),
    match_threshold  FLOAT   DEFAULT 0.65,
    match_count      INT     DEFAULT 20,
    filter_type      TEXT    DEFAULT NULL
)
RETURNS TABLE (
    id          TEXT,
    content     TEXT,
    memory_type TEXT,
    similarity  FLOAT,
    metadata    JSONB,
    session_id  TEXT
)
LANGUAGE plpgsql
AS $$
BEGIN
    RETURN QUERY
    SELECT
        m.id,
        m.content,
        m.memory_type,
        1 - (m.embedding <=> query_embedding) AS similarity,
        m.metadata,
        m.session_id
    FROM agent_memories m
    WHERE
        (filter_type IS NULL OR m.memory_type = filter_type)
        AND 1 - (m.embedding <=> query_embedding) > match_threshold
    ORDER BY m.embedding <=> query_embedding
    LIMIT match_count;
END;
$$;

-- Trigger para updated_at automático
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER memories_updated_at
    BEFORE UPDATE ON agent_memories
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
