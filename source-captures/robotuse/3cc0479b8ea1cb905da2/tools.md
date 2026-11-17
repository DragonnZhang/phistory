# RobotUse — Toolset source

Complete copies of the selected upstream source files, including prompt templates and tool registration code. These are source archives, not assembled system messages or captured API tool schemas. No CLI or model request was executed.

Source tool declarations and registration code; enabled runtime tool schemas are not inferred.

## src/tools/base_tool.py

Source: https://github.com/robotuse-team/RobotUse/blob/60bdf7b31b9a9a45dbc4809d678c008c724f465b/src/tools/base_tool.py

SHA-256: `2a8847bcfd6dcb3eaeba205302100b6bc2a5afe1b16cfa2dfff2864868de53c9`

```
"""Small, runtime-independent contracts for registered tool adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from copy import deepcopy
import inspect
import re
from typing import Any, Callable, Mapping

from .schema import object_schema, validate_schema, validate_value


class ToolRegistrationError(ValueError):
    """An installed tool package has an incomplete or ambiguous definition."""


DispatchCallback = Callable[[str, Mapping[str, Any]], Any]


@dataclass(frozen=True)
class ToolExecutionContext:
    """Delegate execution to the existing boundary, retaining its state checks."""

    dispatch: DispatchCallback

    def __post_init__(self) -> None:
        if not callable(self.dispatch):
            raise TypeError("tool execution requires a dispatch callback")


ToolHandler = Callable[[ToolExecutionContext, str, Mapping[str, Any]], Any]
_NAME = re.compile(r"[a-z][a-z0-9_]*\Z")
_ARGUMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


@dataclass(frozen=True)
class ToolSpec:
    """One public contract and its existing execution name.

    ``input_schema`` owns every required and optional field. It may be a
    factory for role/configuration-dependent contracts such as ``finish``.
    The provider and agent boundary both consume this declaration.
    ``visibility`` records whether a tool is used internally by a workflow.
    Role and task permissions remain with the agent's existing tool inventory.
    """

    name: str
    required_arguments: tuple[str, ...]
    handler: ToolHandler
    internal_name: str | None = None
    kind: str = "tool"
    visibility: str = "agent"
    input_schema: Mapping[str, Any] | Callable[..., Mapping[str, Any]] | None = None
    description: str | Callable[..., str] | None = None
    argument_validators: Mapping[str, Callable[[Any, Mapping[str, Any]], Any]] = field(default_factory=dict)

    @property
    def dispatch_name(self) -> str:
        return self.internal_name or self.name

    def schema(self, *, context=None, role=None, tools=()) -> dict[str, Any]:
        declared = self.input_schema
        if callable(declared):
            declared = declared(context=context, role=role, tools=tools)
        if declared is None:
            if self.required_arguments:
                raise ToolRegistrationError(f"{self.name}: missing input_schema")
            declared = object_schema({})
        return deepcopy(dict(declared))

    def function_schema(self, *, context=None, role=None, tools=(), name=None):
        description = self.description
        if callable(description):
            description = description(context=context, role=role, tools=tools)
        public_name = name or self.name
        return {"type": "function", "function": {
            "name": public_name,
            "description": description or f"Typed {public_name}; use opaque current-session references only.",
            "parameters": self.schema(context=context, role=role, tools=tools),
        }}

    def validate_argument(self, key, value, *, context=None):
        properties = self.schema(context=context)["properties"]
        if key not in properties:
            from src.core.contracts import BoundaryError
            raise BoundaryError(f"{self.name}: undeclared argument {key}")
        validator = self.argument_validators.get(key)
        return validator(value, properties[key]) if validator else validate_value(properties[key], value, key)

    def validate(self) -> None:
        for label, value in (("name", self.name), ("internal_name", self.internal_name)):
            if value is None and label == "internal_name":
                continue
            if not isinstance(value, str) or not _NAME.fullmatch(value):
                raise ToolRegistrationError(f"invalid tool {label}: {value!r}")
        if not isinstance(self.required_arguments, tuple):
            raise ToolRegistrationError(f"{self.name}: required_arguments must be a tuple")
        if any(not isinstance(key, str) or not _ARGUMENT.fullmatch(key)
               for key in self.required_arguments):
            raise ToolRegistrationError(f"{self.name}: invalid required argument name")
        if len(set(self.required_arguments)) != len(self.required_arguments):
            raise ToolRegistrationError(f"{self.name}: duplicate required argument")
        if self.kind not in ("tool", "control", "backend"):
            raise ToolRegistrationError(f"{self.name}: invalid tool kind {self.kind!r}")
        if self.visibility not in ("agent", "internal"):
            raise ToolRegistrationError(f"{self.name}: invalid visibility {self.visibility!r}")
        if self.kind == "backend" and self.visibility != "internal":
            raise ToolRegistrationError(f"{self.name}: backend tools must remain internal")
        if not callable(self.handler):
            raise ToolRegistrationError(f"{self.name}: handler must be callable")
        try:
            inspect.signature(self.handler).bind(None, self.dispatch_name, {})
        except (TypeError, ValueError) as exc:
            raise ToolRegistrationError(
                f"{self.name}: handler must accept (context, name, arguments)"
            ) from exc
        try:
            schema = self.schema()
            validate_schema(schema, self.name)
            if schema.get("type") != "object":
                raise ValueError(f"{self.name}: input_schema must describe an object")
            if any(not isinstance(key, str) or not _ARGUMENT.fullmatch(key)
                   for key in schema["properties"]):
                raise ValueError(f"{self.name}: invalid input property name")
            if self.kind != "control" and tuple(schema["required"]) != self.required_arguments:
                raise ValueError(f"{self.name}: input_schema required fields must match required_arguments")
            for key, validator in self.argument_validators.items():
                if key not in schema["properties"] or not callable(validator):
                    raise ValueError(f"{self.name}: invalid argument validator for {key}")
                inspect.signature(validator).bind(None, {})
        except (TypeError, ValueError) as exc:
            raise ToolRegistrationError(str(exc)) from exc


def dispatch_existing(
    context: ToolExecutionContext, name: str, arguments: Mapping[str, Any],
) -> Any:
    """Call the supplied boundary without changing arguments or error handling."""
    return context.dispatch(name, arguments)


def define_tool(name, properties, *, required=None, handler=dispatch_existing, **options):
    """Declare a tool once, deriving its ordered argument names from its schema."""
    schema = object_schema(properties, required)
    return ToolSpec(name, tuple(schema["required"]), handler, input_schema=schema, **options)

```

## src/tools/discovery.py

Source: https://github.com/robotuse-team/RobotUse/blob/60bdf7b31b9a9a45dbc4809d678c008c724f465b/src/tools/discovery.py

SHA-256: `9d067f8bc17a2ebd63f14291909a8786fe7a7485a7496a9d2d1df7d2e1b13278`

```
"""Discover every direct tool package, rejecting silently unregistered folders."""

from __future__ import annotations

import importlib
from pathlib import Path

from .base_tool import ToolRegistrationError, ToolSpec
from .registry import ToolRegistry


def discover_tools(package: str = "src.tools") -> ToolRegistry:
    """Load each direct package's ``tool.py`` in deterministic folder order.

    Only entrypoints are imported. Nested third-party checkouts, adapters and
    workers are never traversed. No central list of tool package names exists.
    """
    importlib.invalidate_caches()
    module = importlib.import_module(package)
    paths = getattr(module, "__path__", None)
    if paths is None:
        raise ToolRegistrationError(f"{package} is not a tool package")
    directories: dict[str, Path] = {}
    for root in paths:
        for child in Path(root).iterdir():
            if (not child.is_dir() or child.name in ("__pycache__", "third_party")
                    or child.name.startswith(".")):
                continue
            if child.name in directories:
                raise ToolRegistrationError(f"duplicate tool package: {child.name}")
            directories[child.name] = child
    definitions = []
    for name, directory in sorted(directories.items()):
        if not name.isidentifier():
            raise ToolRegistrationError(f"invalid tool package name: {name}")
        if not (directory / "__init__.py").is_file():
            raise ToolRegistrationError(f"{directory}: missing __init__.py")
        if not (directory / "tool.py").is_file():
            raise ToolRegistrationError(f"{directory}: missing tool.py registration")
        entrypoint = f"{package}.{name}.tool"
        try:
            entry = importlib.import_module(entrypoint)
        except Exception as exc:
            raise ToolRegistrationError(f"{entrypoint}: tool entrypoint failed to import") from exc
        tools = getattr(entry, "TOOLS", None)
        if not isinstance(tools, tuple) or not tools:
            raise ToolRegistrationError(f"{entrypoint}: TOOLS must be a nonempty tuple")
        registered = {id(spec) for spec in tools}
        omitted = [binding for binding, value in vars(entry).items()
                   if isinstance(value, ToolSpec) and id(value) not in registered]
        if omitted:
            raise ToolRegistrationError(
                f"{entrypoint}: ToolSpec declarations omitted from TOOLS: {', '.join(sorted(omitted))}"
            )
        definitions.extend(tools)
    return ToolRegistry(definitions)

```

## src/tools/list.py

Source: https://github.com/robotuse-team/RobotUse/blob/60bdf7b31b9a9a45dbc4809d678c008c724f465b/src/tools/list.py

SHA-256: `22194a3fb67d8ff57829b78381a1a01e70d7a3249c1fc937f0c00b7d6aa26a75`

```
"""Read the discovered tool catalog without manually maintaining tool names."""

from .base_tool import ToolSpec
from .discovery import discover_tools
from .registry import ToolRegistry


def list_tools(registry: ToolRegistry | None = None) -> tuple[ToolSpec, ...]:
    """Return public definitions, including marked internal workflow tools."""
    registry = discover_tools() if registry is None else registry
    return tuple(registry.tools.values())

```

## src/tools/registry.py

Source: https://github.com/robotuse-team/RobotUse/blob/60bdf7b31b9a9a45dbc4809d678c008c724f465b/src/tools/registry.py

SHA-256: `2e37b24424c21d3815564db87ba46ac14957b1e2f9688479cc80aafac8565606`

```
"""Validated immutable lookup, independent of agent and simulator imports."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from types import MappingProxyType
from typing import Any

from .base_tool import ToolExecutionContext, ToolRegistrationError, ToolSpec


class ToolRegistry:
    """Reject incomplete registrations before a session starts.

    Internal aliases resolve to the same definition as public names, so the
    existing workflow's private execution calls retain their original path.
    """

    def __init__(self, definitions: Iterable[ToolSpec]) -> None:
        by_name: dict[str, ToolSpec] = {}
        lookup: dict[str, ToolSpec] = {}
        aliases: dict[str, str] = {}
        for spec in definitions:
            if not isinstance(spec, ToolSpec):
                raise ToolRegistrationError("every registered tool must be a ToolSpec")
            spec.validate()
            names = tuple(dict.fromkeys((spec.name, spec.dispatch_name)))
            for name in names:
                if name in lookup:
                    raise ToolRegistrationError(f"duplicate tool name or alias: {name}")
            by_name[spec.name] = spec
            for name in names:
                lookup[name] = spec
            if spec.dispatch_name != spec.name:
                aliases[spec.name] = spec.dispatch_name
        if not by_name:
            raise ToolRegistrationError("tool registry cannot be empty")
        self._tools = MappingProxyType(by_name)
        self._lookup = MappingProxyType(lookup)
        self._aliases = MappingProxyType(aliases)

    @property
    def tools(self) -> Mapping[str, ToolSpec]:
        return self._tools

    @property
    def aliases(self) -> Mapping[str, str]:
        return self._aliases

    def __getitem__(self, name: str) -> ToolSpec:
        try:
            return self._lookup[name]
        except KeyError:
            raise ToolRegistrationError(f"unregistered tool: {name}") from None

    def __contains__(self, name: str) -> bool:
        return name in self._lookup

    def __iter__(self) -> Iterator[str]:
        return iter(self._tools)

    def __len__(self) -> int:
        return len(self._tools)

    def require(self, names: Iterable[str]) -> tuple[str, ...]:
        """Validate membership while preserving caller-owned role/task order."""
        result = tuple(names)
        for name in result:
            self[name]
        return result

    def required_arguments(self, name: str) -> tuple[str, ...]:
        return self[name].required_arguments

    def optional_arguments(self, name: str, *, context=None) -> tuple[str, ...]:
        schema = self[name].schema(context=context)
        return tuple(key for key in schema["properties"] if key not in schema["required"])

    def function_schema(self, name: str, *, context=None, role=None, tools=()):
        return self[name].function_schema(name=name, context=context, role=role, tools=tools)

    def validate_argument(self, name, key, value, *, context=None):
        return self[name].validate_argument(key, value, context=context)

    def dispatch(
        self, name: str, arguments: Mapping[str, Any], *, context: ToolExecutionContext,
    ) -> Any:
        """Execute through the existing boundary; role checks stay at its caller."""
        spec = self[name]
        return spec.handler(context, spec.dispatch_name, arguments)

```

## src/tools/schema.py

Source: https://github.com/robotuse-team/RobotUse/blob/60bdf7b31b9a9a45dbc4809d678c008c724f465b/src/tools/schema.py

SHA-256: `a751522a43882ebc372976380b866943645a374fbf191ea7c100a53dfb326e56`

```
"""Small JSON-schema helpers shared by tool-owned input contracts.

There is deliberately no tool-name or argument-name table here. A new tool
declares all of its fields in its own package; the same schema drives provider
serialization and validation at the agent boundary.
"""
from copy import deepcopy
import math
from collections.abc import Mapping

from src.core.contracts import BoundaryError


def object_schema(properties, required=None, **metadata):
    return dict(type="object", properties=deepcopy(properties),
                required=list(properties if required is None else required),
                additionalProperties=False, **metadata)


def text_schema(**metadata):
    return dict(type="string", **metadata)


def number_schema(**metadata):
    return dict(type="number", **metadata)


def bounded_text(value, schema):
    """Retain tools whose stateful handler owns enum rejection and feedback."""
    return validate_value({"type": "string"}, value, "text")


def normalized_pixel(value, schema):
    """Preserve the original int/float value for normalized image coordinates."""
    validate_value(schema, value, "coordinate")
    return value


def validate_value(schema, value, name):
    """Validate the declared JSON types, retaining RobotUse's finite-number boundary."""
    kinds = schema.get("type")
    kinds = kinds if isinstance(kinds, list) else [kinds]
    if value is None and "null" in kinds:
        return None
    if "number" in kinds or "integer" in kinds:
        if type(value) not in (int, float) or not math.isfinite(value):
            raise BoundaryError(f"{name} must be a finite number")
        if "number" not in kinds and type(value) is not int:
            raise BoundaryError(f"{name} must be an integer")
        if not schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf):
            raise BoundaryError(f"{name} is outside its declared range")
        result = float(value) if "number" in kinds else value
    elif "string" in kinds:
        if not isinstance(value, str) or not value.strip() or len(value) > schema.get("maxLength", 8192):
            raise BoundaryError("expected bounded nonempty string")
        if len(value) < schema.get("minLength", 0):
            raise BoundaryError(f"{name} is shorter than its declared length")
        result = value
    elif "boolean" in kinds:
        if type(value) is not bool:
            raise BoundaryError(f"{name} must be a boolean")
        result = value
    elif "array" in kinds:
        if not isinstance(value, (list, tuple)):
            raise BoundaryError(f"{name} must be an array")
        if not schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", math.inf):
            raise BoundaryError(f"{name} has an invalid number of items")
        result = [validate_value(schema["items"], item, f"{name}[{index}]")
                  for index, item in enumerate(value)]
    elif "object" in kinds:
        if not isinstance(value, Mapping):
            raise BoundaryError(f"{name} must be an object")
        properties = schema.get("properties", {})
        if not set(schema.get("required", ())) <= set(value):
            raise BoundaryError(f"{name} is missing required fields")
        if schema.get("additionalProperties") is False and not set(value) <= set(properties):
            raise BoundaryError(f"{name} contains undeclared fields")
        result = {key: validate_value(properties[key], item, f"{name}.{key}")
                  if key in properties else item for key, item in value.items()}
    else:
        raise BoundaryError(f"{name} has no supported declared type")
    if "enum" in schema and value not in schema["enum"]:
        raise BoundaryError(f"{name} must be one of {schema['enum']}")
    return result


def validate_schema(schema, name):
    """Reject malformed declarations at discovery, before runtime imports."""
    if not isinstance(schema, Mapping):
        raise ValueError(f"{name}: input schema must be a mapping")
    declared = schema.get("type")
    kinds = declared if isinstance(declared, list) else [declared]
    allowed = {"object", "array", "string", "number", "integer", "boolean", "null"}
    if not kinds or any(not isinstance(kind, str) or kind not in allowed for kind in kinds):
        raise ValueError(f"{name}: input schema requires an explicit supported type")
    if len(kinds) > 1 and (len(kinds) != 2 or "null" not in kinds):
        raise ValueError(f"{name}: only nullable type unions are supported")
    supported = {"type", "description", "properties", "required", "additionalProperties", "items",
                 "minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems", "enum"}
    if unknown := set(schema) - supported:
        raise ValueError(f"{name}: unsupported schema keywords: {sorted(unknown)}")
    for key in ("minimum", "maximum", "minLength", "maxLength", "minItems", "maxItems"):
        if key in schema and (type(schema[key]) not in (int, float) or not math.isfinite(schema[key])):
            raise ValueError(f"{name}: {key} must be a finite numeric bound")
    for lower, upper in (("minimum", "maximum"), ("minLength", "maxLength"), ("minItems", "maxItems")):
        if schema.get(lower, -math.inf if lower == "minimum" else 0) > schema.get(upper, math.inf):
            raise ValueError(f"{name}: {lower} exceeds {upper}")
    if "enum" in schema and (not isinstance(schema["enum"], (list, tuple)) or not schema["enum"]):
        raise ValueError(f"{name}: enum must contain allowed values")
    if "object" in kinds:
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, Mapping) or not isinstance(required, (list, tuple)):
            raise ValueError(f"{name}: object schema requires properties and required")
        if len(set(required)) != len(required) or not set(required) <= set(properties):
            raise ValueError(f"{name}: required fields must be declared exactly once")
        if schema.get("additionalProperties") is not False:
            raise ValueError(f"{name}: object schema must reject undeclared fields")
        for key, value in properties.items():
            validate_schema(value, f"{name}.{key}")
    if "array" in kinds:
        validate_schema(schema.get("items"), f"{name}[]")

```
