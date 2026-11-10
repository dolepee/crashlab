"""CrashLab — Hermes testing workbench."""
from __future__ import annotations

import json
import shlex
import shutil
import subprocess
from pathlib import Path

try:
    from . import crashlab_core as core
    from . import campaign_plugin
    from . import repair_workflow
    from . import completion_reminder
    from . import approval
except ImportError:
    import crashlab_core as core
    import campaign_plugin
    import repair_workflow
    import completion_reminder
    import approval

TOOL_SCHEMAS = [
    {
        "name": "crashlab_validate",
        "description": "Validate a CrashLab test contract without running it.",
        "parameters": {
            "type": "object",
            "properties": {"contract_path": {"type": "string", "minLength": 1}},
            "required": ["contract_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "crashlab_run",
        "description": "Run one saved CrashLab case against a web app and inspect persisted state.",
        "parameters": {
            "type": "object",
            "properties": {
                "contract_path": {"type": "string", "minLength": 1},
                "base_url": {"type": "string"},
            },
            "required": ["contract_path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "crashlab_compare",
        "description": "Run the same saved case against a buggy and a repaired app; pass only if the buggy version fails and the repaired version passes.",
        "parameters": {
            "type": "object",
            "properties": {
                "contract_path": {"type": "string", "minLength": 1},
                "buggy_url": {"type": "string", "minLength": 1},
                "fixed_url": {"type": "string", "minLength": 1},
            },
            "required": ["contract_path", "buggy_url", "fixed_url"],
            "additionalProperties": False,
        },
    },
    {
        "name": "crashlab_list",
        "description": "List bundled and configured workspace cases, including self-starting fixtures and setup requirements.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]
TOOL_SCHEMAS.extend(campaign_plugin.schemas())
TOOL_SCHEMAS.extend(repair_workflow.schemas())
TOOL_SCHEMAS.append(completion_reminder.schema())


def _public_arguments(name: str, kwargs):
    """Keep only the published parameters; reject internal or unknown keywords.

    The handler is the boundary between model/API arguments and internal calls,
    so nothing a caller supplies can reach a private keyword of an inner route.
    """
    schema = next((item for item in TOOL_SCHEMAS if item["name"] == name), None)
    allowed = set(((schema or {}).get("parameters") or {}).get("properties") or {})
    if not isinstance(kwargs, dict):
        raise ValueError(f"{name} arguments must be an object")
    extra = sorted(set(kwargs) - allowed)
    if extra:
        raise ValueError(f"{name} does not accept argument(s): {', '.join(extra)}")
    return {key: value for key, value in kwargs.items() if key in allowed}


def _prepare_git_error(project_path):
    """Return an actionable Git prerequisite error without changing app state.

    This is presentation only: it runs the same repository-root query that the
    frozen evaluator already requires, before any experiment directory is made.
    It never initialises Git and never writes to the project or data directory.
    """
    if not isinstance(project_path, str) or not project_path.strip():
        raise ValueError("project_path must be an existing directory for the app you want to test")
    project = Path(project_path).expanduser().resolve()
    if not project.is_dir():
        raise ValueError(f"project_path must be an existing directory: {project}")
    git = shutil.which("git")
    if not git:
        raise ValueError(
            "Git is required to prepare a CrashLab experiment, but `git` was not found on PATH. "
            "Install Git and retry. CrashLab does not initialise an app for you."
        )
    try:
        check = subprocess.run(
            [git, "-C", str(project), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            "The Git prerequisite check timed out. Check that the project is on a responsive "
            "local filesystem and retry."
        ) from exc
    except OSError as exc:
        raise ValueError(
            "Git could not be executed. Install or repair Git on PATH and retry; "
            "CrashLab does not initialise an app for you."
        ) from exc
    if check.returncode != 0:
        raise ValueError(
            "project_path must be a Git repository root. If this app should be a repository, "
            "run `git init` in it; otherwise pass the existing repository root. "
            "CrashLab does not initialise apps for you."
        )
    top = Path((check.stdout or "").strip()).expanduser().resolve()
    if top != project:
        raise ValueError(
            f"project_path must be the Git repository root, not a nested folder. Use {top} instead."
        )


def _handler(name: str):
    def invoke(kwargs, context):
        kwargs = _public_arguments(name, kwargs)
        if name == "crashlab_prepare":
            _prepare_git_error(kwargs.get("project_path"))
        session_id = str(context.get("session_id") or "")
        task_id = str(context.get("task_id") or "")
        # The host binds the originating call id for the dispatch but does not
        # pass it as a handler kwarg; read it from the host, never from model args.
        tool_call_id = approval.host_call_id(context)
        context = {**context, "tool_call_id": tool_call_id}
        if name in {"crashlab_run", "crashlab_compare"}:
            approved = approval.prepare_approved_case(
                name, kwargs, session_id=session_id, task_id=task_id,
                tool_call_id=tool_call_id)
        if name == "crashlab_experiment_watch":
            return completion_reminder.watch(**kwargs)
        if name in {schema["name"] for schema in repair_workflow.schemas()}:
            try:
                return repair_workflow.invoke(name, kwargs, approval_context=context)
            except (FileNotFoundError, subprocess.CalledProcessError) as exc:
                if name == "crashlab_prepare":
                    raise ValueError(
                        "The Git prerequisite check failed while reading the project. "
                        "Confirm that Git is installed and that project_path is the repository root; "
                        "CrashLab did not initialise the app or create an experiment."
                    ) from exc
                raise
        if name.startswith("crashlab_campaign_"):
            return campaign_plugin.invoke(name, kwargs, approval_context=context)
        if name == "crashlab_validate":
            contract = core.load_contract(kwargs["contract_path"])
            return {"ok": True, "contract": contract}
        if name == "crashlab_run":
            return core.run_and_persist(
                approved["path"], base_url=kwargs.get("base_url"),
                frozen_contract=approved["contract"])
        if name == "crashlab_compare":
            # Resolve against the approved snapshot, never the mutable path: a
            # retargeted symlink here would point the relative observers at
            # another directory's database after the approval was granted.
            return core.compare(approved["path"], kwargs["buggy_url"], kwargs["fixed_url"],
                                frozen_contract=approved["contract"])
        if name == "crashlab_list":
            return {"contracts": core.list_contracts()}
        raise ValueError(f"unknown CrashLab tool: {name}")
    def call(args, **context):
        try:
            result = invoke(args, context)
        except approval.ApprovalRequired as exc:
            result = {"ok": False, "error": str(exc), "needs_approval": True}
        return json.dumps(result)
    return call


def _verdict(run: dict) -> str:
    return run.get("status") or ("ERROR" if run.get("error") else "PASS" if run.get("ok") else "FAIL")


def _slash(raw_args: str) -> str:
    try:
        args = shlex.split(raw_args)
    except ValueError:
        return 'Usage: /crashlab run "path/to/case.json" (close any quoted path)'
    if not args or args[0] in ("status", "latest"):
        run = core.latest_run()
        if not run:
            return "CrashLab: no saved run yet. Start a new approved run with the crashlab_run tool."
        state = _verdict(run)
        return (f"CrashLab · last measured {state} · {run.get('contract', '?')}\n"
                f"{run.get('observed', '?')} observed / {run.get('expected', '?')} expected\n"
                f"{run.get('error') or run.get('diagnosis') or 'No diagnosis recorded.'}\n"
                f"{run.get('evidence_path') or ''}").strip()
    if args[0] in ("runs", "log"):
        rows = core.list_runs(10)
        if not rows:
            return "CrashLab: no saved runs."
        return "\n".join(
            f"{row.get('run_id', '?')} · {_verdict(row)} · "
            f"{row.get('contract', '?')} · {row.get('observed', '?')}/{row.get('expected', '?')}"
            for row in rows)
    if args[0] == "run":
        if len(args) != 2:
            return "Usage: /crashlab run <case.json>"
        path = args[1]
        try:
            approved = approval.prepare_approved_case("crashlab_run", {"contract_path": path})
            result = core.run_and_persist(approved["path"], frozen_contract=approved["contract"])
        except approval.ApprovalRequired:
            return ("CrashLab needs a human approval for this case. Use the crashlab_run "
                    "tool from Hermes so the approval gate can show a bounded summary of the case, command, working directory and targets.")
        return (f"CrashLab · {_verdict(result)} · {result.get('contract')}\n"
                f"{result.get('error') or result.get('diagnosis') or 'No diagnosis recorded.'}\n{result.get('evidence_path')}")
    if args[0] in ("help", "?"):
        return ("/crashlab — latest result\n"
                "/crashlab runs — recent runs\n"
                "/crashlab run <case.json> — consume an existing approval grant; otherwise directs to crashlab_run")
    return "Unknown CrashLab command. Try /crashlab help."


def register(ctx):
    if hasattr(ctx, "register_hook"):
        ctx.register_hook("pre_verify", completion_reminder.pre_verify)
        ctx.register_hook("pre_tool_call", approval.pre_tool_call)
        ctx.register_hook("post_approval_response", approval.post_approval_response)
    register_middleware = getattr(ctx, "register_middleware", None)
    if register_middleware is not None:
        register_middleware("tool_execution", approval.tool_execution_middleware)
    from pathlib import Path
    if hasattr(ctx, "register_skill"):
        ctx.register_skill("crashlab", Path(__file__).parent / "skills" / "crashlab" / "SKILL.md", description="Reproduce and verify a local app repair with frozen assertions and current source evidence.")
    for schema in TOOL_SCHEMAS:
        ctx.register_tool(name=schema["name"], toolset="crashlab",
                          schema=schema, handler=_handler(schema["name"]))
    register_command = getattr(ctx, "register_command", None)
    if register_command is not None:
        register_command("crashlab", _slash,
                         description="Show saved evidence; slash commands do not acquire execution approval.",
                         args_hint="[status|runs|run <case.json> (existing grant only)]")
