from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

DB_PATH = Path(__file__).parent.parent / "data" / "factlens.db"


class Database:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    fact_count INTEGER DEFAULT 0,
                    uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS facts (
                    id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL,
                    claim TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    value TEXT,
                    period TEXT,
                    subject TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    page INTEGER NOT NULL,
                    excerpt TEXT NOT NULL,
                    document_name TEXT NOT NULL,
                    normalized_value REAL,
                    currency TEXT,
                    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS relations (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    left_fact_id TEXT NOT NULL,
                    right_fact_id TEXT NOT NULL,
                    left_fact_json TEXT NOT NULL,
                    right_fact_json TEXT NOT NULL,
                    FOREIGN KEY (left_fact_id) REFERENCES facts(id) ON DELETE CASCADE,
                    FOREIGN KEY (right_fact_id) REFERENCES facts(id) ON DELETE CASCADE
                );

                CREATE TABLE IF NOT EXISTS user_feedback (
                    relation_id TEXT PRIMARY KEY,
                    action TEXT NOT NULL, -- 'approve', 'reject', 'override'
                    override_type TEXT,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS diagnostics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    type TEXT NOT NULL,
                    document TEXT NOT NULL,
                    message TEXT NOT NULL
                );

                CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(
                    id UNINDEXED,
                    claim,
                    subject,
                    excerpt
                );
            """)
            conn.commit()

    def clear(self):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executescript("""
                DELETE FROM user_feedback;
                DELETE FROM relations;
                DELETE FROM facts;
                DELETE FROM facts_fts;
                DELETE FROM documents;
                DELETE FROM diagnostics;
            """)
            conn.commit()

    def save_knowledge(self, documents: List[Dict], facts: List[Dict], relations: List[Dict], diagnostics: List[Dict]):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            for doc in documents:
                cursor.execute(
                    "INSERT OR REPLACE INTO documents (id, name, fact_count) VALUES (?, ?, ?)",
                    (doc["id"], doc["name"], doc.get("facts", 0))
                )

            for fact in facts:
                ev = fact.get("evidence", {})
                cursor.execute(
                    """INSERT OR REPLACE INTO facts 
                       (id, document_id, claim, kind, value, period, subject, confidence, page, excerpt, document_name, normalized_value, currency)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        fact["id"],
                        ev.get("document_id", ""),
                        fact["claim"],
                        fact["kind"],
                        fact.get("value"),
                        fact.get("period"),
                        fact.get("subject", ""),
                        fact.get("confidence", 0.0),
                        ev.get("page", 1),
                        ev.get("excerpt", ""),
                        ev.get("document_name", ""),
                        fact.get("normalized_value"),
                        fact.get("currency")
                    )
                )
                cursor.execute(
                    "INSERT OR REPLACE INTO facts_fts (id, claim, subject, excerpt) VALUES (?, ?, ?, ?)",
                    (fact["id"], fact["claim"], fact.get("subject", ""), ev.get("excerpt", ""))
                )

            for rel in relations:
                cursor.execute(
                    """INSERT OR REPLACE INTO relations 
                       (id, type, reason, confidence, left_fact_id, right_fact_id, left_fact_json, right_fact_json)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        rel["id"],
                        rel["type"],
                        rel["reason"],
                        rel["confidence"],
                        rel["left"]["id"],
                        rel["right"]["id"],
                        json.dumps(rel["left"]),
                        json.dumps(rel["right"])
                    )
                )

            for diag in diagnostics:
                cursor.execute(
                    "INSERT INTO diagnostics (type, document, message) VALUES (?, ?, ?)",
                    (diag["type"], diag["document"], diag["message"])
                )
            conn.commit()

    def set_feedback(self, relation_id: str, action: str, override_type: Optional[str] = None):
        with self.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """INSERT OR REPLACE INTO user_feedback (relation_id, action, override_type, updated_at)
                   VALUES (?, ?, ?, CURRENT_TIMESTAMP)""",
                (relation_id, action, override_type)
            )
            conn.commit()

    def get_knowledge(self) -> Dict[str, Any]:
        with self.get_connection() as conn:
            cursor = conn.cursor()

            # Fetch documents
            cursor.execute("SELECT id, name, fact_count FROM documents")
            documents = [dict(row) for row in cursor.fetchall()]

            # Fetch facts
            cursor.execute("SELECT * FROM facts")
            facts_rows = cursor.fetchall()
            facts = []
            for row in facts_rows:
                f_dict = dict(row)
                f_dict["evidence"] = {
                    "document_id": f_dict.pop("document_id"),
                    "document_name": f_dict.pop("document_name"),
                    "page": f_dict.pop("page"),
                    "excerpt": f_dict.pop("excerpt"),
                }
                facts.append(f_dict)

            # Fetch feedback lookup
            cursor.execute("SELECT relation_id, action, override_type FROM user_feedback")
            feedback = {row["relation_id"]: dict(row) for row in cursor.fetchall()}

            # Fetch relations
            cursor.execute("SELECT * FROM relations")
            rel_rows = cursor.fetchall()
            relations = []
            for row in rel_rows:
                r_dict = dict(row)
                r_dict["left"] = json.loads(r_dict["left_fact_json"])
                r_dict["right"] = json.loads(r_dict["right_fact_json"])
                del r_dict["left_fact_json"]
                del r_dict["right_fact_json"]

                # Apply feedback if present
                fb = feedback.get(r_dict["id"])
                if fb:
                    r_dict["user_action"] = fb["action"]
                    if fb["action"] == "override" and fb.get("override_type"):
                        r_dict["type"] = fb["override_type"]
                        r_dict["reason"] = f"Manually overridden to {fb['override_type']} by reviewer."
                else:
                    r_dict["user_action"] = None

                relations.append(r_dict)

            # Fetch diagnostics
            cursor.execute("SELECT type, document, message FROM diagnostics")
            diagnostics = [dict(row) for row in cursor.fetchall()]

            return {
                "documents": documents,
                "facts": facts,
                "relations": relations,
                "diagnostics": diagnostics,
            }
