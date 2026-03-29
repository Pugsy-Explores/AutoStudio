"""SQLite-backed graph storage for symbol nodes and edges."""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


class GraphStorage:
    """SQLite storage for symbol graph: nodes and edges."""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._init_schema()
        return self._conn

    def _init_schema(self):
        conn = self._conn
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                type TEXT,
                file TEXT NOT NULL,
                start_line INTEGER,
                end_line INTEGER,
                docstring TEXT,
                type_info TEXT,
                signature TEXT
            );
            CREATE TABLE IF NOT EXISTS edges (
                source_id INTEGER NOT NULL,
                target_id INTEGER NOT NULL,
                edge_type TEXT NOT NULL,
                FOREIGN KEY (source_id) REFERENCES nodes(id),
                FOREIGN KEY (target_id) REFERENCES nodes(id)
            );
            CREATE INDEX IF NOT EXISTS idx_nodes_name ON nodes(name);
            CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file);
            CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
            CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
        """)
        # Migration: add type_info, signature if missing
        try:
            conn.execute("ALTER TABLE nodes ADD COLUMN type_info TEXT")
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute("ALTER TABLE nodes ADD COLUMN signature TEXT")
        except sqlite3.OperationalError:
            pass
        conn.commit()

    def add_node(self, node_dict: dict) -> int:
        """Insert a node; return its id."""
        import json

        conn = self._connect()
        type_info = node_dict.get("type_info")
        if isinstance(type_info, dict):
            type_info = json.dumps(type_info)
        elif type_info is None:
            type_info = "{}"
        signature = node_dict.get("signature") or ""
        cur = conn.execute(
            """
            INSERT INTO nodes (name, type, file, start_line, end_line, docstring, type_info, signature)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node_dict.get("symbol_name") or node_dict.get("name", ""),
                node_dict.get("symbol_type") or node_dict.get("type", ""),
                node_dict.get("file", ""),
                node_dict.get("start_line"),
                node_dict.get("end_line"),
                node_dict.get("docstring", ""),
                type_info,
                signature,
            ),
        )
        conn.commit()
        return cur.lastrowid

    def add_edge(self, source_id: int, target_id: int, edge_type: str):
        """Insert an edge."""
        conn = self._connect()
        conn.execute(
            "INSERT INTO edges (source_id, target_id, edge_type) VALUES (?, ?, ?)",
            (source_id, target_id, edge_type),
        )
        conn.commit()

    def get_symbol(self, symbol_id: int) -> dict | None:
        """Get node by id."""
        conn = self._connect()
        row = conn.execute("SELECT * FROM nodes WHERE id = ?", (symbol_id,)).fetchone()
        return dict(row) if row else None

    def get_symbol_by_name(self, name: str) -> dict | None:
        """Get first node matching name (exact)."""
        conn = self._connect()
        row = conn.execute("SELECT * FROM nodes WHERE name = ? LIMIT 1", (name,)).fetchone()
        return dict(row) if row else None

    def list_nodes_by_exact_name(self, name: str, limit: int = 200) -> list[dict]:
        """All nodes with exact symbol name (for disambiguation)."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM nodes WHERE name = ? LIMIT ?",
            (name, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def count_call_graph_degree(self, symbol_id: int) -> int:
        """Total in+out edges with call types (for disambiguation tie-break)."""
        conn = self._connect()
        inc = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE target_id = ? AND edge_type IN ('calls', 'call_graph')",
            (symbol_id,),
        ).fetchone()[0]
        outc = conn.execute(
            "SELECT COUNT(*) FROM edges WHERE source_id = ? AND edge_type IN ('calls', 'call_graph')",
            (symbol_id,),
        ).fetchone()[0]
        return int(inc) + int(outc)

    def get_symbols_like(self, pattern: str, limit: int = 10) -> list[dict]:
        """Get nodes where name LIKE %pattern%."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT * FROM nodes WHERE name LIKE ? LIMIT ?",
            (f"%{pattern}%", limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_all_nodes(self) -> list[dict]:
        """Get all nodes for indexing (e.g. BM25)."""
        conn = self._connect()
        rows = conn.execute("SELECT * FROM nodes").fetchall()
        return [dict(r) for r in rows]

    def remove_nodes_for_file(self, file_path: str) -> list[int]:
        """
        Remove all nodes for the given file and edges referencing them.
        Returns list of removed node ids.
        """
        conn = self._connect()
        path_str = str(Path(file_path).resolve())
        rows = conn.execute("SELECT id FROM nodes WHERE file = ?", (path_str,)).fetchall()
        ids = [r[0] for r in rows]
        if not ids:
            return []
        placeholders = ",".join("?" * len(ids))
        conn.execute(f"DELETE FROM edges WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})", ids + ids)
        conn.execute(f"DELETE FROM nodes WHERE id IN ({placeholders})", ids)
        conn.commit()
        return ids

    def get_neighbors(self, symbol_id: int, direction: str = "out", edge_types: list[str] | None = None) -> list[dict]:
        """
        Get adjacent nodes. direction: "out" (outgoing), "in" (incoming), "both".
        edge_types: filter by edge type(s).
        """
        conn = self._connect()
        if direction == "out":
            sql = "SELECT DISTINCT n.* FROM nodes n JOIN edges e ON n.id = e.target_id WHERE e.source_id = ?"
        elif direction == "in":
            sql = "SELECT DISTINCT n.* FROM nodes n JOIN edges e ON n.id = e.source_id WHERE e.target_id = ?"
        else:
            sql = """
                SELECT DISTINCT n.* FROM nodes n JOIN edges e ON
                ((e.source_id = ? AND n.id = e.target_id) OR (e.target_id = ? AND n.id = e.source_id))
            """
        params: list = [symbol_id]
        if direction == "both":
            params.append(symbol_id)
        if edge_types:
            placeholders = ",".join("?" * len(edge_types))
            sql += f" AND e.edge_type IN ({placeholders})"
            params.extend(edge_types)
        rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
