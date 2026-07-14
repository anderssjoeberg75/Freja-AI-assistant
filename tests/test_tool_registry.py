"""Tests for the decorator-based tool registry (Issue #26).

These lock in that the single registry stays the source of truth for all three historically
hand-synced structures, that the Pydantic path auto-generates a Gemini-shaped declaration,
and that execute() applies arg hygiene + validation.
"""

import asyncio

import pytest
from pydantic import BaseModel, Field
from typing import Optional

from backend.services import tool_registry as tr


def test_derived_structures_cover_every_tool():
    """Declarations, permission keys and executors are all derived from one registry, so
    every declared tool automatically has a matching permission key and executor."""
    names = {d["name"] for d in tr.TOOL_DECLARATIONS}
    assert names == set(tr.EXECUTOR_MAP.keys())
    # Every tool has a permission gate (the class of "forgot to add a key" bug is gone).
    assert names == set(tr.TOOL_PERMISSION_KEYS.keys())
    # Spot-check a couple of well-known tools survived the refactor.
    assert "get_weather" in names
    assert "publish_instagram_post" in names


def test_declarations_are_gemini_shaped():
    for decl in tr.TOOL_DECLARATIONS:
        assert set(decl.keys()) == {"name", "description", "parameters"}
        assert decl["parameters"]["type"] == "OBJECT"
        assert "properties" in decl["parameters"]


def test_duplicate_registration_is_rejected():
    reg = tr.ToolRegistry()
    reg.add("dup", "d", executor=lambda a: a, parameters={"type": "OBJECT", "properties": {}})
    with pytest.raises(ValueError):
        reg.add("dup", "d2", executor=lambda a: a, parameters={"type": "OBJECT", "properties": {}})


def test_clean_schema_upper_cases_and_collapses_optional():
    class M(BaseModel):
        a: str = Field(description="required field")
        b: Optional[int] = Field(default=None, description="optional field")

    params = tr._params_from_pydantic(M)
    assert params["type"] == "OBJECT"
    assert params["properties"]["a"]["type"] == "STRING"
    # Optional[int] -> {"anyOf": [int, null]} collapses to INTEGER, no anyOf/title/default left.
    assert params["properties"]["b"]["type"] == "INTEGER"
    assert "anyOf" not in params["properties"]["b"]
    assert "title" not in params["properties"]["b"]
    assert params["required"] == ["a"]


def test_execute_reports_validation_error_for_missing_required_arg():
    # google_search has a Pydantic args_schema requiring 'query'.
    result = asyncio.run(tr.execute_tool("google_search", {}))
    assert "error" in result
    assert "query" in result["error"]


def test_execute_unknown_tool_returns_error():
    result = asyncio.run(tr.execute_tool("does_not_exist", {}))
    assert "not registered" in result["error"]


def test_execute_drops_none_and_empty_args():
    """Registry hygiene drops None/empty values so a tool's declared defaults apply."""
    reg = tr.ToolRegistry()
    seen = {}

    @reg.register("echo", "e", parameters={"type": "OBJECT", "properties": {"x": {"type": "STRING"}}})
    async def _echo(args):
        seen.update(args)
        return {"ok": True}

    asyncio.run(reg.execute("echo", {"x": "", "y": None, "z": "keep"}))
    # Empty string and None are stripped; unknown-but-present keys are kept for dict schemas.
    assert "x" not in seen and "y" not in seen
    assert seen.get("z") == "keep"
