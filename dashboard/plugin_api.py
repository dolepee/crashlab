"""CrashLab backend routes for the Desktop evidence pane."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA = Path(os.environ.get("CRASHLAB_DATA_DIR", Path.home() / ".local" / "share" / "crashlab"))
CASE_DIR = Path(os.environ.get("CRASHLAB_CASE_DIR", ROOT / "cases"))
RUN_ID = re.compile(r"^[A-Za-z0-9._-]{1,120}$")


class RunRequest(BaseModel):
    contract_path: str


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"invalid evidence: {path.name}: {exc}"}
    return value if isinstance(value, dict) else {"ok": False, "error": "evidence is not an object"}


@router.get("/overview")
async def overview() -> dict:
    """Compact status for the Desktop pane; reads saved evidence only."""
    comparison = _read(DATA / "latest.json") if (DATA / "latest.json").exists() else None
    latest_run = _read(DATA / "latest-run.json") if (DATA / "latest-run.json").exists() else None
    campaign = _read(DATA / "campaign-latest.json") if (DATA / "campaign-latest.json").exists() else None
    return {
        "ok": True,
        "comparison": comparison,
        "latest_run": latest_run,
        "campaign": campaign,
    }


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


def _case_paths() -> list[Path]:
    paths = []
    for directory in (ROOT / "contracts", CASE_DIR):
        if directory.is_dir():
            paths.extend(sorted(directory.glob("*.json")))
    return paths


@router.get("/contracts")
async def contracts() -> dict:
    rows = []
    for path in _case_paths():
        try:
            contract = json.loads(path.read_text(encoding="utf-8"))
            rows.append({"id": contract.get("id", path.stem), "path": str(path),
                         "source": "bundled" if path.is_relative_to(ROOT / "contracts") else "workspace"})
        except (OSError, ValueError) as exc:
            rows.append({"id": path.stem, "path": str(path), "error": str(exc)})
    return {"contracts": rows}


@router.post("/run")
async def run(request: RunRequest) -> dict:
    """Start one saved case through the same core path as the agent tool."""
    from crashlab_core import persist_run, run_contract

    allowed = {path.resolve() for path in _case_paths()}
    path = Path(request.contract_path).expanduser().resolve()
    if path not in allowed:
        raise HTTPException(status_code=400, detail="contract must be in the case directory")
    result = await asyncio.to_thread(run_contract, str(path))
    return persist_run(result)
