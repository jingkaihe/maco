from __future__ import annotations

from pathlib import Path

from maco.codegen import _sanitize_identifier, _schema_type_source, _typed_dict_source


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
