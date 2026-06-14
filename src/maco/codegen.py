"""Generate Python code interfaces for MCP tools."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
import json
import keyword
from pathlib import Path
import re
import shutil
from typing import Any, cast

from jinja2 import Environment, PackageLoader, StrictUndefined

from .config import MacoConfig
from .mcp_manager import MCPManager


_CODEGEN_TEMPLATES = Environment(
    loader=PackageLoader("maco", "templates"),
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=True,
    undefined=StrictUndefined,
)


def _pyrepr(value: Any) -> str:
    return repr(value)


_CODEGEN_TEMPLATES.filters["pyrepr"] = _pyrepr


@dataclass(frozen=True)
class GenerationStats:
    server_count: int
    tool_count: int
    workspace: Path


@dataclass(frozen=True)
class TypeSource:
    """Generated Python type source for one JSON schema."""

    source: str
    type_expr: str
    is_model: bool = False


async def generate_async(
    config: MacoConfig,
    workspace: str | Path = ".maco",
    server_filter: str | None = None,
    clean: bool = False,
) -> GenerationStats:
    """Generate Python wrappers for all configured MCP tools."""

    workspace_path = Path(workspace).expanduser().resolve()
    if clean and workspace_path.exists():
        shutil.rmtree(workspace_path)
    generated_pkg = workspace_path / "maco_generated"
    servers_pkg = generated_pkg / "servers"
    servers_pkg.mkdir(parents=True, exist_ok=True)

    async with MCPManager(config) as manager:
        tools_by_server = await manager.list_tools(server_filter=server_filter)

    _write_workspace_pyproject(workspace_path)
    _write_template(
        generated_pkg / "__init__.py",
        "codegen/package_init.py.j2",
        docstring="Generated MCP wrappers for maco.",
    )
    _write_template(
        servers_pkg / "__init__.py",
        "codegen/package_init.py.j2",
        docstring="Generated MCP server packages.",
    )
    (generated_pkg / "py.typed").write_text("", encoding="utf-8")
    _write_client(generated_pkg / "client.py")

    manifest = {
        "version": 1,
        "config": str(config.path),
        "servers": [],
    }

    server_module_names = _unique_sanitized_names(tools_by_server.keys())
    server_count = 0
    tool_count = 0

    for server_name, tools in sorted(tools_by_server.items()):
        server_module = server_module_names[server_name]
        server_dir = servers_pkg / server_module
        server_dir.mkdir(parents=True, exist_ok=True)
        tool_module_names = _unique_sanitized_names(tool["name"] for tool in tools)

        exports: list[str] = []
        server_manifest = {
            "name": server_name,
            "module": server_module,
            "tools": [],
        }
        for tool in sorted(tools, key=lambda item: item["name"]):
            tool_name = tool["name"]
            func_name = tool_module_names[tool_name]
            module_path = server_dir / f"{func_name}.py"
            _write_tool(module_path, server_name, tool, func_name)
            exports.append(func_name)
            server_manifest["tools"].append(
                {
                    "name": tool_name,
                    "function": func_name,
                    "module": f"maco_generated.servers.{server_module}.{func_name}",
                    "description": tool.get("description") or "",
                }
            )
            tool_count += 1

        _write_server_init(server_dir / "__init__.py", exports)
        manifest["servers"].append(server_manifest)
        server_count += 1

    (workspace_path / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    return GenerationStats(
        server_count=server_count,
        tool_count=tool_count,
        workspace=workspace_path,
    )


def generate(
    config: MacoConfig,
    workspace: str | Path = ".maco",
    server_filter: str | None = None,
    clean: bool = False,
) -> GenerationStats:
    return asyncio.run(generate_async(config, workspace, server_filter, clean))


def _render_template(template_name: str, **context: Any) -> str:
    return _CODEGEN_TEMPLATES.get_template(template_name).render(**context)


def _render_source(template_name: str, **context: Any) -> str:
    return _render_template(template_name, **context).rstrip()


def _write_template(path: Path, template_name: str, **context: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_render_template(template_name, **context), encoding="utf-8")


def _write_workspace_pyproject(workspace: Path) -> None:
    _write_template(workspace / "pyproject.toml", "codegen/pyproject.toml.j2")


def _write_client(path: Path) -> None:
    _write_template(path, "codegen/client.py.j2")


def _write_tool(path: Path, server_name: str, tool: dict[str, Any], func_name: str) -> None:
    tool_name = tool["name"]
    description = tool.get("description") or ""
    input_schema = tool.get("inputSchema") or {"type": "object", "properties": {}}
    output_schema = tool.get("outputSchema")
    input_type = _schema_type_source(f"{_class_name(func_name)}Input", input_schema)
    output_type = _schema_type_source(
        f"{_class_name(func_name)}Output",
        output_schema,
        missing_type_expr="_t.Any",
    )
    return_expr = _return_expr(output_type)
    _write_template(
        path,
        "codegen/tool.py.j2",
        description=description,
        docstring=_docstring(description, input_schema, output_schema),
        func_name=func_name,
        input_is_model=input_type.is_model,
        input_type_expr=input_type.type_expr,
        input_type_source=input_type.source,
        output_type_expr=output_type.type_expr,
        output_type_source=output_type.source,
        return_expr=return_expr,
        server_name=server_name,
        tool_name=tool_name,
    )


def _write_server_init(path: Path, exports: list[str]) -> None:
    _write_template(path, "codegen/server_init.py.j2", exports=exports)


def _typed_dict_source(class_name: str, schema: dict[str, Any]) -> str:
    """Backward-compatible helper used by tests and older callers."""

    return _schema_type_source(class_name, schema).source


def _schema_type_source(
    root_name: str,
    schema: Any,
    *,
    missing_type_expr: str = "dict[str, _t.Any]",
) -> TypeSource:
    if not isinstance(schema, dict):
        root_type = _class_name(root_name)
        return TypeSource(_render_type_alias(root_type, missing_type_expr), root_type)
    used_names: set[str] = set()
    return _schema_to_type(_class_name(root_name), schema, schema, used_names, define_named=True)


def _schema_to_type(
    type_name: str,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    used_names: set[str],
    *,
    define_named: bool = False,
) -> TypeSource:
    schema = _resolve_schema_ref(schema, root_schema)

    if "const" in schema:
        return _maybe_alias(type_name, _literal_type([schema["const"]]), used_names, define_named)
    if isinstance(schema.get("enum"), list) and schema["enum"]:
        return _maybe_alias(type_name, _literal_type(schema["enum"]), used_names, define_named)

    for key in ("oneOf", "anyOf"):
        variants = schema.get(key)
        if isinstance(variants, list) and variants:
            definitions: list[str] = []
            type_exprs: list[str] = []
            for index, variant in enumerate(variants, start=1):
                if not isinstance(variant, dict):
                    type_exprs.append("_t.Any")
                    continue
                variant_schema = cast("dict[str, Any]", variant)
                variant_type = _schema_to_type(
                    f"{type_name}Variant{index}",
                    variant_schema,
                    root_schema,
                    used_names,
                )
                definitions.append(variant_type.source)
                type_exprs.append(variant_type.type_expr)
            return _maybe_alias(
                type_name,
                _union_type(type_exprs),
                used_names,
                define_named,
                definitions,
            )

    all_of = schema.get("allOf")
    if isinstance(all_of, list) and len(all_of) == 1 and isinstance(all_of[0], dict):
        return _schema_to_type(type_name, all_of[0], root_schema, used_names, define_named=define_named)

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        definitions = []
        type_exprs = []
        for item in schema_type:
            item_schema = {**schema, "type": item}
            item_type = _schema_to_type(type_name, item_schema, root_schema, used_names)
            definitions.append(item_type.source)
            type_exprs.append(item_type.type_expr)
        return _maybe_alias(
            type_name,
            _union_type(type_exprs),
            used_names,
            define_named,
            definitions,
        )

    if schema_type == "object" or "properties" in schema:
        return _object_type_source(type_name, schema, root_schema, used_names, define_named=define_named)
    if schema_type == "array":
        items = schema.get("items")
        if isinstance(items, dict):
            item_type = _schema_to_type(
                f"{type_name}Item",
                items,
                root_schema,
                used_names,
            )
            return _maybe_alias(
                type_name,
                f"list[{item_type.type_expr}]",
                used_names,
                define_named,
                [item_type.source],
            )
        return _maybe_alias(type_name, "list[_t.Any]", used_names, define_named)
    if schema_type == "string":
        return _maybe_alias(type_name, "str", used_names, define_named)
    if schema_type == "integer":
        return _maybe_alias(type_name, "int", used_names, define_named)
    if schema_type == "number":
        return _maybe_alias(type_name, "float", used_names, define_named)
    if schema_type == "boolean":
        return _maybe_alias(type_name, "bool", used_names, define_named)
    if schema_type == "null":
        return _maybe_alias(type_name, "None", used_names, define_named)
    return _maybe_alias(type_name, "_t.Any", used_names, define_named)


def _object_type_source(
    type_name: str,
    schema: dict[str, Any],
    root_schema: dict[str, Any],
    used_names: set[str],
    *,
    define_named: bool,
) -> TypeSource:
    properties = schema.get("properties")
    if isinstance(properties, dict) and properties:
        reserved_name = _reserve_type_name(type_name, used_names)
        required = {field for field in schema.get("required", []) if isinstance(field, str)}
        definitions: list[str] = []
        fields: list[dict[str, str]] = []
        used_fields: set[str] = set()
        for raw_prop_name, raw_prop_schema in sorted(properties.items()):
            prop_name = str(raw_prop_name)
            prop_schema = cast("dict[str, Any]", raw_prop_schema if isinstance(raw_prop_schema, dict) else {})
            prop_type = _schema_to_type(
                f"{reserved_name}{_class_name(str(prop_name))}",
                prop_schema,
                root_schema,
                used_names,
            )
            definitions.append(prop_type.source)
            default = _field_default(prop_name, prop_schema, required)
            nullable = _is_nullable(prop_schema)
            type_expr = prop_type.type_expr
            if prop_name not in required or nullable:
                type_expr = _optional_type(type_expr)
            field_name = _safe_field_name(prop_name, used_fields)
            field_args = _field_args(prop_name, prop_schema, default, field_name)
            fields.append(
                {
                    "field_args": field_args,
                    "name": field_name,
                    "type_expr": type_expr,
                }
            )
        definitions.append(_render_source("codegen/model.py.j2", class_name=reserved_name, fields=fields))
        return TypeSource(_join_definitions(definitions), reserved_name, is_model=True)

    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        value_type = _schema_to_type(f"{type_name}Value", additional, root_schema, used_names)
        return _maybe_alias(
            type_name,
            f"dict[str, {value_type.type_expr}]",
            used_names,
            define_named,
            [value_type.source],
        )

    return _maybe_alias(type_name, "dict[str, _t.Any]", used_names, define_named)


def _resolve_schema_ref(schema: dict[str, Any], root_schema: dict[str, Any]) -> dict[str, Any]:
    ref = schema.get("$ref")
    if not isinstance(ref, str) or not ref.startswith("#/"):
        return schema
    target: Any = root_schema
    for part in ref[2:].split("/"):
        part = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(target, dict) or part not in target:
            return schema
        target = target[part]
    if not isinstance(target, dict):
        return schema
    merged = dict(target)
    merged.update({key: value for key, value in schema.items() if key != "$ref"})
    return merged


def _maybe_alias(
    type_name: str,
    type_expr: str,
    used_names: set[str],
    define_named: bool,
    definitions: list[str] | None = None,
) -> TypeSource:
    definitions = definitions or []
    if not define_named:
        return TypeSource(_join_definitions(definitions), type_expr)
    reserved_name = _reserve_type_name(type_name, used_names)
    if _is_root_model_expr(type_expr):
        return TypeSource(
            _join_definitions(
                [
                    *definitions,
                    _render_source("codegen/root_model.py.j2", class_name=reserved_name, type_expr=type_expr),
                ]
            ),
            reserved_name,
            is_model=True,
        )
    return TypeSource(_join_definitions([*definitions, _render_type_alias(reserved_name, type_expr)]), reserved_name)


def _render_type_alias(type_name: str, type_expr: str) -> str:
    return _render_source("codegen/type_alias.py.j2", type_name=type_name, type_expr=type_expr)


def _is_root_model_expr(type_expr: str) -> bool:
    return type_expr not in {"_t.Any", "None"} and not type_expr.startswith("dict[")


def _field_default(prop_name: str, schema: dict[str, Any], required: set[str]) -> str:
    if "default" in schema:
        return repr(schema["default"])
    return "..." if prop_name in required else "None"


def _field_args(prop_name: str, schema: dict[str, Any], default: str, field_name: str) -> str:
    kwargs = [f"default={default}"]
    if field_name != prop_name:
        kwargs.append(f"alias={prop_name!r}")
    description = schema.get("description")
    if isinstance(description, str) and description:
        kwargs.append(f"description={description!r}")
    title = schema.get("title")
    if isinstance(title, str) and title:
        kwargs.append(f"title={title!r}")
    for schema_key, field_key in (
        ("minimum", "ge"),
        ("maximum", "le"),
        ("exclusiveMinimum", "gt"),
        ("exclusiveMaximum", "lt"),
        ("minLength", "min_length"),
        ("maxLength", "max_length"),
        ("pattern", "pattern"),
    ):
        if schema_key in schema:
            kwargs.append(f"{field_key}={schema[schema_key]!r}")
    return f"Field({', '.join(kwargs)})"


def _safe_field_name(name: str, used_fields: set[str]) -> str:
    candidate = re.sub(r"\W", "_", name)
    if not candidate or candidate[0].isdigit():
        candidate = f"field_{candidate}"
    if keyword.iskeyword(candidate):
        candidate += "_"
    base = candidate
    index = 2
    while candidate in used_fields:
        candidate = f"{base}_{index}"
        index += 1
    used_fields.add(candidate)
    return candidate


def _optional_type(type_expr: str) -> str:
    if "None" in type_expr.split(" | "):
        return type_expr
    return f"{type_expr} | None"


def _is_nullable(schema: dict[str, Any]) -> bool:
    schema_type = schema.get("type")
    return schema_type == "null" or (isinstance(schema_type, list) and "null" in schema_type)


def _return_expr(output_type: TypeSource) -> str:
    if output_type.type_expr == "_t.Any":
        return "result"
    if output_type.is_model:
        return f"{output_type.type_expr}.model_validate(result)"
    return f"_t.cast({output_type.type_expr}, result)"


def _literal_type(values: list[Any]) -> str:
    return "_t.Literal[{}]".format(", ".join(repr(value) for value in values))


def _union_type(type_exprs: list[str]) -> str:
    unique = []
    for expr in type_exprs:
        if expr and expr not in unique:
            unique.append(expr)
    if not unique:
        return "_t.Any"
    if len(unique) == 1:
        return unique[0]
    return " | ".join(unique)


def _join_definitions(definitions: list[str]) -> str:
    return "\n\n".join(definition for definition in definitions if definition)


def _reserve_type_name(type_name: str, used_names: set[str]) -> str:
    base = _class_name(type_name)
    candidate = base
    index = 2
    while candidate in used_names:
        candidate = f"{base}{index}"
        index += 1
    used_names.add(candidate)
    return candidate


def _docstring(description: str, input_schema: dict[str, Any], output_schema: Any) -> str:
    del input_schema, output_schema
    return (description.strip() or "Call the MCP tool.").replace('"""', '\"\"\"')


def _unique_sanitized_names(names: Any) -> dict[str, str]:
    originals = list(names)
    base_names = [_sanitize_identifier(name) for name in originals]
    counts: Counter[str] = Counter()
    result: dict[str, str] = {}
    for original, base in zip(originals, base_names, strict=True):
        counts[base] += 1
        result[original] = base if counts[base] == 1 else f"{base}_{counts[base]}"
    return result


def _sanitize_identifier(name: str) -> str:
    words = [part for part in re.split(r"[^0-9A-Za-z]+", name.strip()) if part]
    if not words:
        result = "tool"
    else:
        result = words[0].lower() + "".join(part[:1].upper() + part[1:] for part in words[1:])
    result = re.sub(r"\W", "_", result)
    if result[0].isdigit():
        result = f"_{result}"
    if keyword.iskeyword(result):
        result += "_"
    return result


def _class_name(func_name: str) -> str:
    parts = [part for part in re.split(r"[^0-9A-Za-z]+", str(func_name)) if part]
    result = "".join(part[:1].upper() + part[1:] for part in parts) or "Tool"
    if result[0].isdigit():
        result = f"_{result}"
    if keyword.iskeyword(result):
        result += "Type"
    return result
