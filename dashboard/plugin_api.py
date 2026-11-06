"""CrashLab backend routes for the Desktop evidence pane."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("CRASHLAB_DATA_DIR", Path.home() / ".local" / "share" / "crashlab"))
RUN_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"invalid evidence: {path.name}: {exc}"}
    return value if isinstance(value, dict) else {"ok": False, "error": "evidence is not an object"}


@router.get("/evidence")
async def evidence() -> dict:
    path = DATA / "latest.json"
    if not path.exists():
        return {"ok": False, "error": "no completed comparison yet"}
    return {"ok": True, "run": _read(path)}


@router.get("/runs")
async def runs() -> dict:
    rows = []
    directory = DATA / "runs"
    if directory.is_dir():
        for path in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            item = _read(path)
            rows.append({
                "run_id": item.get("run_id", path.stem),
                "contract": item.get("contract"),
                "ok": item.get("ok"),
                "mtime": path.stat().st_mtime,
            })
    return {"runs": rows[:50]}


@router.get("/runs/{run_id}")
async def run_detail(run_id: str) -> dict:
    if not RUN_ID.fullmatch(run_id):
        raise HTTPException(status_code=400, detail="invalid run id")
    path = DATA / "runs" / f"{run_id}.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="run not found")
    return _read(path)


@router.get("/contracts")
async def contracts() -> dict:
    rows = []
    for path in sorted((ROOT / "contracts").glob("*.json")):
        rows.append({"id": path.stem, "path": str(path)})
    return {"contracts": rows}
