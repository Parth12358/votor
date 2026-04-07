# Analytics and persistence helpers for tracking Votor query usage, token stats, costs, and file access.
# This file contains functions and classes for analyzing and processing data within the Votor application.
import json
import json
from pathlib import Path
from datetime import datetime
from typing import Optional

DB_PATH = Path(".vectormind/analytics.db")


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS queries (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp           TEXT NOT NULL,
    question            TEXT NOT NULL,
    model               TEXT NOT NULL,
    input_tokens        INTEGER NOT NULL,
    output_tokens       INTEGER NOT NULL,
    total_tokens        INTEGER NOT NULL,
    cost                REAL NOT NULL,
    response_time       REAL NOT NULL,
    retrieval_score     REAL NOT NULL,
    chunks_retrieved    INTEGER NOT NULL,
    full_context_tokens INTEGER NOT NULL,
    tokens_saved        INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS daily_stats (
    date                TEXT PRIMARY KEY,
    total_queries       INTEGER DEFAULT 0,
    total_tokens        INTEGER DEFAULT 0,
    total_cost          REAL DEFAULT 0.0,
    total_tokens_saved  INTEGER DEFAULT 0,
    avg_retrieval_score REAL DEFAULT 0.0,
    avg_response_time   REAL DEFAULT 0.0
);

CREATE TABLE IF NOT EXISTS file_access (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL,
    file_path   TEXT NOT NULL,
    query_id    INTEGER,
    score       REAL
);
"""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create tables if they don't exist."""
    with get_connection() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

def log_query(
    question: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    total_tokens: int,
    cost: float,
    response_time: float,
    retrieval_score: float,
    chunks_retrieved: int,
    full_context_tokens: int,
    tokens_saved: int,
    file_accesses: Optional[list[dict]] = None
):
    """Log a completed query to analytics DB."""
    init_db()

    timestamp = datetime.utcnow().isoformat()
    today = datetime.utcnow().date().isoformat()

    with get_connection() as conn:
        # Insert query record
        cursor = conn.execute(
            """
            INSERT INTO queries (
                timestamp, question, model,
                input_tokens, output_tokens, total_tokens,
                cost, response_time, retrieval_score,
                chunks_retrieved, full_context_tokens, tokens_saved
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp, question, model,
                input_tokens, output_tokens, total_tokens,
                cost, response_time, retrieval_score,
                chunks_retrieved, full_context_tokens, tokens_saved
            )
        )
        query_id = cursor.lastrowid

        # Log file accesses
        if file_accesses:
            conn.executemany(
                """
                INSERT INTO file_access (timestamp, file_path, query_id, score)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (timestamp, fa["file"], query_id, fa.get("score", 0.0))
                    for fa in file_accesses
                ]
            )

        # Upsert daily stats
        conn.execute(
            """
            INSERT INTO daily_stats (
                date, total_queries, total_tokens, total_cost,
                total_tokens_saved, avg_retrieval_score, avg_response_time
            ) VALUES (?, 1, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                total_queries       = total_queries + 1,
                total_tokens        = total_tokens + excluded.total_tokens,
                total_cost          = total_cost + excluded.total_cost,
                total_tokens_saved  = total_tokens_saved + excluded.total_tokens_saved,
                avg_retrieval_score = (avg_retrieval_score * total_queries + excluded.avg_retrieval_score) / (total_queries + 1),
                avg_response_time   = (avg_response_time * total_queries + excluded.avg_response_time) / (total_queries + 1)
            """,
            (today, total_tokens, cost, tokens_saved, retrieval_score, response_time)
        )


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

def get_summary() -> dict:
    """Overall stats summary for status command."""
    init_db()
    with get_connection() as conn:
        row = conn.execute(
            """
            SELECT
                COUNT(*)            AS total_queries,
                SUM(total_tokens)   AS total_tokens,
                SUM(cost)           AS total_cost,
                AVG(response_time)  AS avg_response_time,
                AVG(retrieval_score) AS avg_retrieval_score,
                SUM(tokens_saved)   AS total_tokens_saved
            FROM queries
            """
        ).fetchone()

        if not row or row["total_queries"] == 0:
            return {
                "total_queries": 0,
                "total_tokens": 0,
                "total_cost": 0.0,
                "avg_response_time": 0.0,
                "avg_retrieval_score": 0.0,
                "total_tokens_saved": 0
            }

        return dict(row)


def get_recent_queries(limit: int = 20) -> list[dict]:
    """Fetch most recent queries for dashboard."""
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM queries
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_daily_stats(days: int = 30) -> list[dict]:
    """Fetch daily stats for dashboard charts."""
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT * FROM daily_stats
            ORDER BY date DESC
            LIMIT ?
            """,
            (days,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]


def get_top_files(limit: int = 10) -> list[dict]:
    """Most accessed files across all queries."""
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                file_path,
                COUNT(*)        AS access_count,
                AVG(score)      AS avg_score
            FROM file_access
            GROUP BY file_path
            ORDER BY access_count DESC
            LIMIT ?
            """,
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_model_breakdown() -> list[dict]:
    """Token and cost breakdown by model."""
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                model,
                COUNT(*)            AS total_queries,
                SUM(total_tokens)   AS total_tokens,
                SUM(cost)           AS total_cost,
                AVG(retrieval_score) AS avg_retrieval_score
            FROM queries
            GROUP BY model
            ORDER BY total_queries DESC
            """
        ).fetchall()
        return [dict(r) for r in rows]


def get_token_savings_trend(days: int = 30) -> list[dict]:
    """Daily token savings vs full context trend."""
    init_db()
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT
                date,
                total_tokens_saved,
                total_tokens,
                ROUND(
                    CAST(total_tokens_saved AS REAL) /
                    NULLIF(total_tokens + total_tokens_saved, 0) * 100,
                    1
                ) AS savings_pct
            FROM daily_stats
            ORDER BY date DESC
            LIMIT ?
            """,
            (days,)
        ).fetchall()
        return [dict(r) for r in reversed(rows)]