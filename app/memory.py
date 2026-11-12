from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg
import redis
import replicate

from app.config import settings
from app.local_store import LocalStore


logger = logging.getLogger(__name__)


class MemoryService:
    """
    NeuroDev's three-layer memory system.

    Layer 1: Redis
        Short-term session/conversation memory.

    Layer 2: PostgreSQL
        Structured long-term project knowledge, decisions,
        reasoning and provenance.

    Layer 3: pgvector
        Semantic long-term memory using BGE-M3 embeddings.
    """

    def __init__(self) -> None:
        # --------------------------------------------------
        # Layer 1: Redis
        # --------------------------------------------------
        self.redis = redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
        )

        # --------------------------------------------------
        # Layer 2 + 3: PostgreSQL / pgvector
        # --------------------------------------------------
        self.postgres_url = settings.POSTGRES_URL
        self.local_store = LocalStore()
        self.use_local_store = False

        # --------------------------------------------------
        # Embedding Model
        # --------------------------------------------------
        self.embedding_model = settings.EMBEDDING_MODEL

    def _enable_local_storage(self, error: Exception) -> None:
        if not self.use_local_store:
            logger.warning(
                "PostgreSQL is unavailable; using persistent local SQLite storage: %s",
                error,
            )
        self.use_local_store = True

    def create_project(
        self,
        project_id: str,
        name: str,
        description: str | None,
    ) -> None:
        if self.use_local_store:
            self.local_store.create_project(project_id, name, description)
            return
        try:
            with psycopg.connect(
                self.postgres_url,
                connect_timeout=settings.POSTGRES_CONNECT_TIMEOUT,
            ) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO projects (id, name, description)
                        VALUES (%s, %s, %s)
                        """,
                        (project_id, name, description),
                    )
                conn.commit()
        except psycopg.OperationalError as exc:
            self._enable_local_storage(exc)
            self.local_store.create_project(project_id, name, description)

    # ======================================================
    # REDIS - SHORT TERM MEMORY
    # ======================================================

    def save_session_message(
        self,
        session_id: str,
        role: str,
        content: str,
    ) -> None:
        """
        Store a recent conversation message in Redis.

        Redis is intentionally used for temporary conversational
        context rather than permanent project knowledge.
        """

        key = f"neurodev:session:{session_id}"

        message = {
            "role": role,
            "content": content,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        if self.use_local_store:
            self.local_store.save_session_message(session_id, role, content)
            return
        try:
            self.redis.rpush(key, json.dumps(message))

            # Automatically expire inactive sessions.
            self.redis.expire(key, settings.REDIS_TTL)
        except redis.RedisError as exc:
            logger.warning("Redis is unavailable; saving session message locally: %s", exc)
            self.local_store.save_session_message(session_id, role, content)

    def get_session_messages(
        self,
        session_id: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Retrieve recent conversational turns.
        """

        key = f"neurodev:session:{session_id}"

        if self.use_local_store:
            return self.local_store.get_session_messages(session_id, limit)
        try:
            messages = self.redis.lrange(key, max(0, -limit), -1)
        except redis.RedisError as exc:
            logger.warning("Redis is unavailable; reading session history locally: %s", exc)
            return self.local_store.get_session_messages(session_id, limit)

        return [
            json.loads(message)
            for message in messages
        ]

    def clear_session(
        self,
        session_id: str,
    ) -> None:
        """
        Delete temporary session memory.
        """

        try:
            self.redis.delete(f"neurodev:session:{session_id}")
        except redis.RedisError as exc:
            logger.warning("Could not clear the Redis session; clearing local history: %s", exc)
        self.local_store.clear_session(session_id)

    # ======================================================
    # EMBEDDINGS - BGE-M3 / REPLICATE
    # ======================================================

    def generate_embedding(
        self,
        text: str,
    ) -> list[float]:
        """
        Generate a semantic embedding using BGE-M3
        through Replicate.
        """

        output = replicate.run(
            self.embedding_model,
            input={
                "text": text,
            },
        )

        # Replicate model outputs can vary depending on
        # the deployed BGE-M3 version.
        if isinstance(output, list):
            return [float(value) for value in output]

        if hasattr(output, "__iter__"):
            return [float(value) for value in output]

        raise ValueError(
            "Unexpected embedding output from Replicate."
        )

    # ======================================================
    # POSTGRESQL - STRUCTURED LONG TERM MEMORY
    # ======================================================

    def save_decision(
        self,
        project_id: str,
        decision: str,
        reason: str,
        source: str = "developer",
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Store a structured technical decision.

        PostgreSQL stores the actual project fact/decision,
        its reasoning and provenance.
        """

        decision_id = str(uuid.uuid4())

        metadata = metadata or {}

        if self.use_local_store:
            self.local_store.save_decision(
                decision_id,
                project_id,
                decision,
                reason,
                source,
                confidence,
                json.dumps(metadata),
            )
            return decision_id

        try:
            with psycopg.connect(
                self.postgres_url,
                connect_timeout=settings.POSTGRES_CONNECT_TIMEOUT,
            ) as conn:
                with conn.cursor() as cursor:

                    cursor.execute(
                        """
                        INSERT INTO decisions (
                            id, project_id, decision, reason, source, confidence,
                            metadata, created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            decision_id,
                            project_id,
                            decision,
                            reason,
                            source,
                            confidence,
                            json.dumps(metadata),
                            datetime.now(timezone.utc),
                        ),
                    )

                conn.commit()
        except psycopg.OperationalError as exc:
            self._enable_local_storage(exc)
            self.local_store.save_decision(
                decision_id,
                project_id,
                decision,
                reason,
                source,
                confidence,
                json.dumps(metadata),
            )

        return decision_id

    def get_project_decisions(
        self,
        project_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """
        Retrieve structured decisions for a project.
        """

        if self.use_local_store:
            return self.local_store.get_project_decisions(project_id, limit)
        try:
            with psycopg.connect(
                self.postgres_url,
                connect_timeout=settings.POSTGRES_CONNECT_TIMEOUT,
            ) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT id, project_id, decision, reason, source,
                               confidence, metadata, created_at
                        FROM decisions
                        WHERE project_id = %s
                        ORDER BY created_at DESC
                        LIMIT %s
                        """,
                        (project_id, limit),
                    )
                    rows = cursor.fetchall()
        except psycopg.OperationalError as exc:
            self._enable_local_storage(exc)
            return self.local_store.get_project_decisions(project_id, limit)

        return [
            {
                "id": row[0],
                "project_id": row[1],
                "decision": row[2],
                "reason": row[3],
                "source": row[4],
                "confidence": row[5],
                "metadata": row[6],
                "created_at": row[7],
            }
            for row in rows
        ]

    # ======================================================
    # PGVECTOR - SEMANTIC LONG TERM MEMORY
    # ======================================================

    def save_semantic_memory(
        self,
        project_id: str,
        content: str,
        memory_type: str = "general",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Generate BGE-M3 embedding and store the memory
        in PostgreSQL/pgvector.
        """

        memory_id = str(uuid.uuid4())
        metadata = metadata or {}

        if self.use_local_store:
            self.local_store.save_memory(
                memory_id, project_id, content, memory_type, json.dumps(metadata)
            )
            return memory_id

        embedding = self.generate_embedding(content)
        try:
            with psycopg.connect(
                self.postgres_url,
                connect_timeout=settings.POSTGRES_CONNECT_TIMEOUT,
            ) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        INSERT INTO memories (
                            id, project_id, content, memory_type, embedding,
                            metadata, created_at
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            memory_id,
                            project_id,
                            content,
                            memory_type,
                            embedding,
                            json.dumps(metadata),
                            datetime.now(timezone.utc),
                        ),
                    )
                conn.commit()
        except psycopg.OperationalError as exc:
            self._enable_local_storage(exc)
            self.local_store.save_memory(
                memory_id, project_id, content, memory_type, json.dumps(metadata)
            )

        return memory_id

    def search_semantic_memory(
        self,
        project_id: str,
        query: str,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """
        Perform semantic similarity search using pgvector.
        """

        if self.use_local_store:
            return self.local_store.search_memories(project_id, query, limit)

        query_embedding = self.generate_embedding(query)

        try:
            with psycopg.connect(
                self.postgres_url,
                connect_timeout=settings.POSTGRES_CONNECT_TIMEOUT,
            ) as conn:
                with conn.cursor() as cursor:
                    cursor.execute(
                        """
                        SELECT id, content, memory_type, metadata, created_at,
                               1 - (embedding <=> %s::vector) AS similarity
                        FROM memories
                        WHERE project_id = %s
                        ORDER BY embedding <=> %s::vector
                        LIMIT %s
                        """,
                        (query_embedding, project_id, query_embedding, limit),
                    )
                    rows = cursor.fetchall()
        except psycopg.OperationalError as exc:
            self._enable_local_storage(exc)
            return self.local_store.search_memories(project_id, query, limit)

        return [
            {
                "id": row[0],
                "content": row[1],
                "memory_type": row[2],
                "metadata": row[3],
                "created_at": row[4],
                "similarity": float(row[5]),
            }
            for row in rows
        ]

    # ======================================================
    # HYBRID MEMORY RETRIEVAL
    # ======================================================

    def retrieve_relevant_memory(
        self,
        project_id: str,
        query: str,
        session_id: str | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """
        Retrieve context from all three memory layers.

        Returns:
            short_term:
                Recent conversation from Redis.

            decisions:
                Structured technical decisions from PostgreSQL.

            semantic:
                Relevant historical knowledge from pgvector.
        """

        short_term = []

        if session_id:
            short_term = self.get_session_messages(
                session_id=session_id,
                limit=10,
            )

        decisions = self.get_project_decisions(
            project_id=project_id,
            limit=limit,
        )

        semantic = self.search_semantic_memory(
            project_id=project_id,
            query=query,
            limit=limit,
        )

        return {
            "short_term": short_term,
            "decisions": decisions,
            "semantic": semantic,
        }

    # ======================================================
    # SAVE COMPLETE PROJECT MEMORY
    # ======================================================

    def save_memory(
        self,
        project_id: str,
        content: str,
        memory_type: str = "general",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Unified memory-writing method.

        Semantic knowledge goes to pgvector while
        structured decisions are stored separately in
        PostgreSQL.
        """

        return self.save_semantic_memory(
            project_id=project_id,
            content=content,
            memory_type=memory_type,
            metadata=metadata,
        )


# ============================================================
# Singleton Memory Service
# ============================================================

memory_service = MemoryService()