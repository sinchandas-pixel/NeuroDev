from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any


class LocalStore:
    """Persistent SQLite fallback for local development without PostgreSQL."""

    def __init__(self) -> None:
        data_dir = Path(
            os.getenv("NEURODEV_DATA_DIR", str(Path.home() / ".neurodev"))
        )
        self.path = data_dir / "neurodev-local.sqlite3"

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS session_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_local_session_messages
                ON session_messages(session_id, id);
            CREATE TABLE IF NOT EXISTS memories (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                content TEXT NOT NULL,
                memory_type TEXT NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_local_memories_project
                ON memories(project_id, created_at);
            CREATE TABLE IF NOT EXISTS decisions (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                reason TEXT NOT NULL,
                source TEXT NOT NULL,
                confidence REAL NOT NULL,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_local_decisions_project
                ON decisions(project_id, created_at);
            """
        )
        return connection

    def create_project(
        self, project_id: str, name: str, description: str | None
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO projects (id, name, description) VALUES (?, ?, ?)",
                (project_id, name, description),
            )

    def save_session_message(self, session_id: str, role: str, content: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO session_messages (session_id, role, content) VALUES (?, ?, ?)",
                (session_id, role, content),
            )

    def get_session_messages(self, session_id: str, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT role, content, created_at AS timestamp
                FROM (
                    SELECT role, content, created_at, id
                    FROM session_messages
                    WHERE session_id = ?
                    ORDER BY id DESC
                    LIMIT ?
                )
                ORDER BY id ASC
                """,
                (session_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def clear_session(self, session_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM session_messages WHERE session_id = ?",
                (session_id,),
            )

    def get_project_decisions(self, project_id: str, limit: int) -> list[dict[str, Any]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, project_id, decision, reason, source, confidence,
                       metadata, created_at
                FROM decisions
                WHERE project_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (project_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def save_memory(
        self,
        memory_id: str,
        project_id: str,
        content: str,
        memory_type: str,
        metadata: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO memories (id, project_id, content, memory_type, metadata)
                VALUES (?, ?, ?, ?, ?)
                """,
                (memory_id, project_id, content, memory_type, metadata),
            )

    def search_memories(
        self, project_id: str, query: str, limit: int
    ) -> list[dict[str, Any]]:
        terms = {term.casefold() for term in query.split() if len(term) > 2}
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT id, content, memory_type, metadata, created_at
                FROM memories
                WHERE project_id = ?
                ORDER BY created_at DESC
                """,
                (project_id,),
            ).fetchall()
        ranked = []
        for row in rows:
            item = dict(row)
            content_terms = {term.casefold() for term in item["content"].split()}
            score = len(terms & content_terms) / max(len(terms), 1)
            if score:
                item["similarity"] = score
                ranked.append(item)
        return sorted(ranked, key=lambda item: item["similarity"], reverse=True)[:limit]

    def save_decision(
        self,
        decision_id: str,
        project_id: str,
        decision: str,
        reason: str,
        source: str,
        confidence: float,
        metadata: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO decisions
                    (id, project_id, decision, reason, source, confidence, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (decision_id, project_id, decision, reason, source, confidence, metadata),
            )
