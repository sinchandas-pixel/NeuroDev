-- ============================================================
-- NeuroDev Database Schema
-- PostgreSQL + pgvector
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;


-- ============================================================
-- Projects
-- ============================================================

CREATE TABLE IF NOT EXISTS projects (
    id UUID PRIMARY KEY,
    name VARCHAR(255) NOT NULL,
    description TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);


-- ============================================================
-- Decisions
-- Stores long-term technical decisions and their provenance.
-- ============================================================

CREATE TABLE IF NOT EXISTS decisions (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    decision TEXT NOT NULL,
    reason TEXT NOT NULL,

    source VARCHAR(50) NOT NULL DEFAULT 'developer',
    confidence FLOAT NOT NULL DEFAULT 1.0,

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_decisions_project
    ON decisions(project_id);

CREATE INDEX IF NOT EXISTS idx_decisions_created_at
    ON decisions(created_at DESC);


-- ============================================================
-- Memories
-- Semantic long-term project memory.
-- BGE-M3 embeddings are stored using pgvector.
-- ============================================================

CREATE TABLE IF NOT EXISTS memories (
    id UUID PRIMARY KEY,
    project_id UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,

    content TEXT NOT NULL,
    memory_type VARCHAR(50) NOT NULL DEFAULT 'general',

    embedding VECTOR(1024),

    metadata JSONB NOT NULL DEFAULT '{}'::jsonb,

    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_memories_project
    ON memories(project_id);

CREATE INDEX IF NOT EXISTS idx_memories_type
    ON memories(memory_type);

CREATE INDEX IF NOT EXISTS idx_memories_created_at
    ON memories(created_at DESC);


-- ============================================================
-- Vector similarity index
-- ============================================================

CREATE INDEX IF NOT EXISTS idx_memories_embedding
    ON memories
    USING hnsw (embedding vector_cosine_ops);