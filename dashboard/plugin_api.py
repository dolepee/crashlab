"""CrashLab backend routes for the Desktop evidence pane."""
from __future__ import annotations

import json
import os
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent
EVIDENCE = Path(os.environ.get("CRASHLAB_EVIDENCE_DIR", ROOT / "evidence"))


def _load(name: str) -> dict:
    path = EVIDENCE / name
    if not path.exists():
        return {"ok": False, "error": f"missing evidence: {name}"}
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/evidence")
async def evidence() -> dict:
    return {
        "ok": True,
        "repair": _load("repair-compare.json"),
        "booking": _load("booking-fixed.json"),
    }


@router.get("/contracts")
async def contracts() -> dict:
    rows = []
    for path in sorted((ROOT / "contracts").glob("*.json")):
        rows.append({"id": path.stem, "path": str(path)})
    return {"contracts": rows}
