"""Read-only SQLite observer for CrashLab fixtures.

This deliberately does not go through the application's /state endpoint. The
application under repair must not be able to change the observation mechanism.
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) < 3:
        print(json.dumps({"error": "usage: crashlab_probe.py DB TABLE [WHERE]"}))
        return 2
    db = Path(sys.argv[1]).expanduser()
    table = sys.argv[2]
    where = sys.argv[3] if len(sys.argv) > 3 else ""
    if not db.exists():
        print(json.dumps({"error": f"database not found: {db}"}))
        return 2
    if not table.replace("_", "").isalnum():
        print(json.dumps({"error": "invalid table name"}))
        return 2
    query = f"SELECT * FROM {table}"
    if where:
        query += f" WHERE {where}"
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            rows = con.execute(query).fetchall()
            columns = [d[0] for d in con.execute(query).description or []]
        finally:
            con.close()
    except Exception as exc:
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
        return 2
    records = [dict(zip(columns, row)) for row in rows]
    print(json.dumps({"records": records, "count": len(records)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
