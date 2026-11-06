"""Deliberately broken/fixed inventory fixture, bound to loopback only.

The optional test_batch gate is an explicit fixture-only scheduling hook. In
buggy mode it holds both stale reads before either write. In fixed mode both
requests reach the gate before their atomic transaction. It is not a production
API or a claim that a simultaneous client burst always reproduces a server race.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def make_server(db, mode="buggy", port=0):
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    gates = {}
    gate_lock = threading.Lock()

    def connect():
        return sqlite3.connect(db, timeout=5, isolation_level=None)

    def reset():
        with connect() as con:
            con.executescript("""
                CREATE TABLE IF NOT EXISTS inventory (sku TEXT PRIMARY KEY, available INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS reservations (
                    id INTEGER PRIMARY KEY, intent_key TEXT UNIQUE NOT NULL,
                    sku TEXT NOT NULL, units INTEGER NOT NULL);
                DELETE FROM reservations;
                DELETE FROM inventory;
                INSERT INTO inventory VALUES ('widget', 1), ('gadget', 1);
            """)
        with gate_lock:
            gates.clear()

    reset()

    class Handler(BaseHTTPRequestHandler):
        def send(self, status, value):
            if getattr(self, "gate_evidence", None) is not None:
                value = {**value, "_crashlab_gate": self.gate_evidence}
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def gate(self, body):
            batch = body.get("test_batch")
            if not batch:
                return
            participants = body.get("test_participants", 2)
            if type(participants) is not int or not 2 <= participants <= 16:
                raise ValueError("invalid test_participants")
            self.gate_evidence = {"batch": batch, "participants": participants, "released": False}
            with gate_lock:
                barrier = gates.setdefault(batch, threading.Barrier(participants, timeout=3))
            barrier.wait()
            self.gate_evidence["released"] = True

        def do_GET(self):
            if self.path == "/health":
                self.send(200, {"ok": True, "mode": mode})
            else:
                self.send(404, {"error": "not found"})

        def do_POST(self):
            if self.path == "/reset":
                reset()
                self.send(200, {"ok": True})
                return
            if self.path != "/reserve":
                self.send(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 16384:
                    raise ValueError("invalid body length")
                body = json.loads(self.rfile.read(length))
                sku = body["sku"]
                intent = body["intent_key"]
                units = body.get("units", 1)
                if sku not in ("widget", "gadget") or not isinstance(intent, str) or not intent:
                    raise ValueError("unknown SKU or empty intent")
                if type(units) is not int or units != 1:
                    raise ValueError("fixture supports exactly one unit")
            except (ValueError, TypeError, KeyError) as exc:
                self.send(400, {"error": str(exc)})
                return
            con = connect()
            try:
                if mode == "buggy":
                    available = con.execute("SELECT available FROM inventory WHERE sku=?", (sku,)).fetchone()[0]
                    self.gate(body)
                    if available < units:
                        self.send(409, {"error": "out of stock"})
                        return
                    # The defect: decision is made outside the write transaction.
                    # Two requests both read 1, then both write 0 and reserve 1.
                    con.execute("BEGIN IMMEDIATE")
                    con.execute("UPDATE inventory SET available=? WHERE sku=?", (available - units, sku))
                else:
                    self.gate(body)
                    if mode == "reject_all":
                        self.send(409, {"error": "deliberately overrestrictive repair"})
                        return
                    con.execute("BEGIN IMMEDIATE")
                    changed = con.execute(
                        "UPDATE inventory SET available=available-? WHERE sku=? AND available>=?",
                        (units, sku, units)).rowcount
                    if changed != 1:
                        con.rollback()
                        self.send(409, {"error": "out of stock"})
                        return
                cur = con.execute("INSERT INTO reservations(intent_key,sku,units) VALUES(?,?,?)", (intent, sku, units))
                con.commit()
                self.send(201, {"id": cur.lastrowid, "sku": sku, "intent_key": intent})
            except threading.BrokenBarrierError:
                con.rollback()
                self.send(503, {"error": "fixture test gate did not receive all participants"})
            except sqlite3.IntegrityError:
                con.rollback()
                self.send(409, {"error": "duplicate intent"})
            finally:
                con.close()

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True)
    parser.add_argument("--port", type=int, default=8795)
    parser.add_argument("--mode", choices=("buggy", "fixed", "reject_all"), default="buggy")
    args = parser.parse_args()
    server = make_server(args.db, args.mode, args.port)
    print(f"inventory fixture http://127.0.0.1:{server.server_port} mode={args.mode} db={args.db}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
