from __future__ import annotations

from pathlib import Path
import sys
from types import ModuleType
from typing import Any

from maco.codegen import _sanitize_identifier, _schema_type_source, _typed_dict_source, generate_sandbox_sdk


def test_generate_sandbox_sdk_uses_tools_package_layout(tmp_path):
    stats = generate_sandbox_sdk(
        {
            "echo-server": [
                {
                    "name": "echo",
                    "description": "Echo a message",
                    "inputSchema": {
                        "type": "object",
                        "required": ["message"],
                        "properties": {"message": {"type": "string"}},
                    },
                    "outputSchema": {"type": "string"},
                }
            ]
        },
        workspace=tmp_path,
    )

    assert stats.tool_count == 1
    tool_source = (tmp_path / "tools" / "echoServer" / "echo.py").read_text(encoding="utf-8")
    assert "from tools._client import call_mcp_tool" in tool_source
    init_source = (tmp_path / "tools" / "echoServer" / "__init__.py").read_text(encoding="utf-8")
    assert "from .echo import echo" in init_source
    manifest = (tmp_path / "manifest.json").read_text(encoding="utf-8")
    assert '"package": "tools"' in manifest


def test_sanitize_identifier():
    assert _sanitize_identifier("read_file") == "readFile"
    assert _sanitize_identifier("browser-click") == "browserClick"
    assert _sanitize_identifier("123 list") == "_123List"
    assert _sanitize_identifier("class") == "class_"


def test_typed_dict_source_uses_json_property_names():
    source = _typed_dict_source(
        "Input",
        {
            "type": "object",
            "required": ["path-name"],
            "properties": {
                "path-name": {"type": "string"},
                "recursive": {"type": "boolean"},
            },
        },
    )

    assert "class Input(BaseModel):" in source
    assert "path_name: str = Field(default=..., alias='path-name')" in source
    assert "recursive: bool | None = Field(default=None)" in source
    compile(
        "import typing as _t\nfrom pydantic import BaseModel, ConfigDict, Field, RootModel\n"
        + source,
        str(Path("generated.py")),
        "exec",
    )


def test_generated_models_validate_nested_aliases():
    typed = _schema_type_source(
        "SearchInput",
        {
            "type": "object",
            "required": ["query-text", "filters"],
            "properties": {
                "query-text": {"type": "string"},
                "filters": {
                    "type": "object",
                    "required": ["max-results"],
                    "properties": {
                        "max-results": {"type": "integer", "minimum": 1},
                        "include-archived": {"type": "boolean"},
                    },
                },
            },
        },
    )
    namespace = _exec_generated_source(typed.source)

    search_input = namespace["SearchInput"].model_validate(
        {
            "query-text": "mcp",
            "filters": {"max-results": 5},
        }
    )

    assert search_input.query_text == "mcp"
    assert search_input.filters.max_results == 5
    assert search_input.model_dump(by_alias=True, exclude_none=True) == {
        "query-text": "mcp",
        "filters": {"max-results": 5},
    }


def test_schema_type_source_generates_typed_output_aliases():
    typed = _schema_type_source(
        "SearchOutput",
        {
            "type": "object",
            "required": ["items"],
            "properties": {
                "items": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["title", "score"],
                        "properties": {
                            "title": {"type": "string"},
                            "score": {"type": "number"},
                            "tags": {"type": "array", "items": {"type": "string"}},
                        },
                    },
                },
                "status": {"enum": ["ok", "partial"]},
            },
        },
    )

    assert typed.type_expr == "SearchOutput"
    assert typed.is_model
    assert "class SearchOutputItemsItem(BaseModel):" in typed.source
    assert "items: list[SearchOutputItemsItem] = Field(default=...)" in typed.source
    assert "status: _t.Literal['ok', 'partial'] | None = Field(default=None)" in typed.source
    compile(
        "import typing as _t\nfrom pydantic import BaseModel, ConfigDict, Field, RootModel\n"
        + typed.source,
        str(Path("generated.py")),
        "exec",
    )


def test_schema_type_source_generates_root_model_for_scalar_schema():
    typed = _schema_type_source("CountOutput", {"type": "integer"})

    assert typed.type_expr == "CountOutput"
    assert typed.is_model
    assert "class CountOutput(RootModel[int]):" in typed.source
    compile(
        "import typing as _t\nfrom pydantic import BaseModel, ConfigDict, Field, RootModel\n"
        + typed.source,
        str(Path("generated.py")),
        "exec",
    )


def _exec_generated_source(source: str) -> dict[str, Any]:
    module = ModuleType("generated_test_models")
    namespace = module.__dict__
    sys.modules[module.__name__] = module
    exec(
        "import typing as _t\nfrom pydantic import BaseModel, ConfigDict, Field, RootModel\n"
        + source,
        namespace,
    )
    return namespace
