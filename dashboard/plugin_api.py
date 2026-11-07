"""CrashLab backend routes for the Desktop evidence pane."""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import subprocess
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DATA = Path(os.environ.get("CRASHLAB_DATA_DIR", Path.home() / ".local" / "share" / "crashlab"))
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
    from crashlab_core import list_contracts
    return [Path(row['path']) for row in list_contracts()]


@router.get("/contracts")
async def contracts() -> dict:
    from crashlab_core import list_contracts
    return {"contracts": list_contracts()}


@router.post("/run")
async def run(request: RunRequest) -> dict:
    """Start one saved case through the same core path as the agent tool."""
    from crashlab_core import run_and_persist

    allowed = {path.resolve() for path in _case_paths()}
    path = Path(request.contract_path).expanduser().resolve()
    if path not in allowed:
        raise HTTPException(status_code=400, detail="contract must be in the case directory")
    return await asyncio.to_thread(run_and_persist, str(path))


class WatchRequest(BaseModel):
    enabled: bool


def _experiments() -> dict:
    import repair_workflow as workflow
    rows = []
    watched = set()
    for watch in (workflow.core._data_dir() / 'watches').glob('*.json'):
        value = _read(watch)
        if value.get('experiment_id'):
            watched.add(value['experiment_id'])
    all_paths = sorted((workflow.core._data_dir() / 'experiments').glob('*/experiment.json'),
                       key=lambda path: path.stat().st_mtime, reverse=True)
    paths = [path for index, path in enumerate(all_paths) if index < 5 or path.parent.name in watched]
    for path in paths:
        eid = path.parent.name
        try:
            row = workflow.status(eid)
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            saved = _read(path)
            row = {'experiment_id': eid, 'intent': saved.get('intent') if isinstance(saved.get('intent'), str) else None, 'project_path': saved.get('project_path') if isinstance(saved.get('project_path'), str) else None, 'status': 'UNAVAILABLE', 'error': ('Cannot check the project source. Restore access to its Git repository, then refresh.' if isinstance(exc, subprocess.SubprocessError) else 'Experiment evidence is unavailable. Restore the saved case and project, or ask Hermes to prepare a new experiment.'), 'error_detail': str(exc)}
        row['watching'] = eid in watched
        rows.append(row)
    return {'experiments': rows}


@router.get('/experiments')
async def experiments() -> dict:
    return await asyncio.to_thread(_experiments)


@router.post('/experiments/{experiment_id}/run')
async def experiment_run(experiment_id: str) -> dict:
    import repair_workflow as workflow
    try:
        return await asyncio.to_thread(workflow.run, experiment_id)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post('/experiments/{experiment_id}/watch')
async def experiment_watch(experiment_id: str, request: WatchRequest) -> dict:
    import completion_reminder
    try:
        return await asyncio.to_thread(completion_reminder.watch, experiment_id, request.enabled)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
