"""Decorator-based tool registry infrastructure, shared by every tool_registry submodule.

Split out of the former monolithic backend/services/tool_registry.py. Each domain submodule
(weather_search, health_data, calendar, trainer_tools, learning, system, instagram,
codex_aliases) imports `registry` from here and registers its own tools onto the same shared
instance via `@registry.register(...)` / `registry.add(...)`.
"""

import inspect
from pydantic import BaseModel, Field, ValidationError

# JSON-schema keys that Gemini's function-declaration format does not accept and that
# Pydantic emits; stripped by clean_schema().
_GEMINI_STRIP_KEYS = ("title", "default", "additionalProperties", "$defs", "definitions")


def clean_schema(schema: dict) -> dict:
    """Rewrites a JSON schema (e.g. from Pydantic) into the shape Gemini expects.

    Strips keys Gemini rejects (title/default/additionalProperties/$defs), upper-cases the
    JSON `type` names (``string`` -> ``STRING``), collapses an ``anyOf`` of a real type plus
    ``null`` (how Pydantic renders Optional[...]) down to that real type, and recurses into
    ``properties`` and array ``items``."""
    if not isinstance(schema, dict):
        return schema

    # Optional[X] renders as {"anyOf": [<X>, {"type": "null"}]} - collapse to <X>.
    if "anyOf" in schema:
        non_null = [s for s in schema["anyOf"] if s.get("type") != "null"]
        merged = dict(non_null[0]) if len(non_null) == 1 else {}
        for k, v in schema.items():
            if k != "anyOf" and k not in merged:
                merged[k] = v
        schema = merged

    out = {}
    for key, val in schema.items():
        if key in _GEMINI_STRIP_KEYS:
            continue
        if key == "type" and isinstance(val, str):
            out["type"] = val.upper()
        elif key == "properties" and isinstance(val, dict):
            out["properties"] = {k: clean_schema(v) for k, v in val.items()}
        elif key == "items" and isinstance(val, dict):
            out["items"] = clean_schema(val)
        else:
            out[key] = val
    return out


def _params_from_pydantic(args_schema) -> dict:
    """Builds a Gemini OBJECT `parameters` block from a Pydantic model class."""
    cleaned = clean_schema(args_schema.model_json_schema())
    params = {"type": "OBJECT", "properties": cleaned.get("properties", {})}
    if cleaned.get("required"):
        params["required"] = cleaned["required"]
    return params


class ToolSpec:
    """One tool's single definition: declaration + permission key + executor."""
    __slots__ = ("name", "description", "parameters", "permission_key", "executor", "args_schema")

    def __init__(self, name, description, executor, parameters=None, permission_key=None, args_schema=None):
        self.name = name
        self.description = description
        self.executor = executor
        self.permission_key = permission_key
        self.args_schema = args_schema
        if parameters is not None:
            self.parameters = parameters
        elif args_schema is not None:
            self.parameters = _params_from_pydantic(args_schema)
        else:
            self.parameters = {"type": "OBJECT", "properties": {}}

    @property
    def declaration(self) -> dict:
        return {"name": self.name, "description": self.description, "parameters": self.parameters}


def _short_validation_error(exc: ValidationError) -> str:
    """Condenses a Pydantic ValidationError into a one-line, model-actionable hint."""
    parts = []
    for err in exc.errors():
        loc = ".".join(str(p) for p in err.get("loc", ())) or "(args)"
        parts.append(f"{loc}: {err.get('msg', 'invalid')}")
    return "; ".join(parts[:5])


class ToolRegistry:
    """Holds the single source of truth for every tool and derives the legacy structures."""

    def __init__(self):
        self._specs = {}  # name -> ToolSpec (insertion-ordered)

    def register(self, name, description, parameters=None, permission_key=None, args_schema=None):
        """Decorator: registers the decorated coroutine as the executor for `name`."""
        def _decorator(fn):
            self.add(name, description, fn, parameters, permission_key, args_schema)
            return fn
        return _decorator

    def add(self, name, description, executor, parameters=None, permission_key=None, args_schema=None):
        """Registers an executor defined elsewhere (imported impl or alias)."""
        if name in self._specs:
            raise ValueError(f"Tool '{name}' is already registered.")
        self._specs[name] = ToolSpec(name, description, executor, parameters, permission_key, args_schema)

    # --- Derived views (keep the historical public names/behaviour) ---
    @property
    def declarations(self) -> list:
        return [spec.declaration for spec in self._specs.values()]

    @property
    def permission_keys(self) -> dict:
        return {name: spec.permission_key for name, spec in self._specs.items() if spec.permission_key}

    @property
    def executor_map(self) -> dict:
        return {name: spec.executor for name, spec in self._specs.items()}

    def _hygiene(self, spec: ToolSpec, args: dict) -> dict:
        """Drops None/empty values so declared defaults apply. Unknown keys are only pruned
        on the Pydantic path (validation there is authoritative); dict-schema tools keep any
        extra keys their executors read as aliases."""
        cleaned = {k: v for k, v in (args or {}).items() if v is not None and v != ""}
        if spec.args_schema is not None:
            allowed = set(spec.args_schema.model_fields.keys())
            cleaned = {k: v for k, v in cleaned.items() if k in allowed}
        return cleaned

    async def execute(self, name: str, args: dict, progress_callback=None) -> dict:
        spec = self._specs.get(name)
        if not spec:
            return {"error": f"Tool '{name}' is not registered in the system registry."}

        call_args = self._hygiene(spec, args)
        if spec.args_schema is not None:
            try:
                model = spec.args_schema(**call_args)
                call_args = model.model_dump(exclude_none=True)
            except ValidationError as ve:
                return {"error": f"Invalid arguments for '{name}': {_short_validation_error(ve)}"}

        # Long-running tools accept a progress_callback (used by /api/tools/status polling);
        # introspect so short tools keep a plain (args) signature.
        if "progress_callback" in inspect.signature(spec.executor).parameters:
            return await spec.executor(call_args, progress_callback=progress_callback)
        return await spec.executor(call_args)


registry = ToolRegistry()


def Math_round(val):
    """Rounds half away from zero, matching JavaScript's Math.round() on the frontend.

    Python's built-in round() uses banker's rounding (round-half-to-even), so round(0.5)
    is 0 and round(2.5) is 2. Health averages are rendered client-side too, and the two
    must agree."""
    if val is None:
        return None
    return int(val + 0.5) if val >= 0 else int(val - 0.5)
