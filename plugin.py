"""CrashLab — Hermes testing workbench."""
from __future__ import annotations

import json

try:
    from . import crashlab_core as core
except ImportError:
    import crashlab_core as core

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
        "description": "List available CrashLab contracts.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
    },
]


def _handler(name: str):
    def invoke(kwargs):
        if name == "crashlab_validate":
            contract = core.load_contract(kwargs["contract_path"])
            return {"ok": True, "contract": contract}
        if name == "crashlab_run":
            return core.run_contract(kwargs["contract_path"], base_url=kwargs.get("base_url"))
        if name == "crashlab_compare":
            return core.compare(kwargs["contract_path"], kwargs["buggy_url"], kwargs["fixed_url"])
        if name == "crashlab_list":
            return {"contracts": core.list_contracts()}
        raise ValueError(f"unknown CrashLab tool: {name}")
    def call(args):
        return json.dumps(invoke(args))
    return call


def register(ctx):
    for schema in TOOL_SCHEMAS:
        ctx.register_tool(name=schema["name"], toolset="crashlab",
                          schema=schema, handler=_handler(schema["name"]))
