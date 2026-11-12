"""The dftracer import is inserted after the LAST top-level import, a multi-line one included (it used to land inside the parentheses)."""
import ast

from dftracer_agents.mcp_tools.tools.session.annotation_python import _last_import_idx

SRC = '''"""doc"""
from __future__ import annotations
import os
from of2_features import (  # pyright: ignore[reportImplicitRelativeImport]
    A,
    B,
)

X = 1
'''


def test_insert_after_a_multiline_import():
    lines = SRC.split("\n")
    i = _last_import_idx(lines)
    out = lines[:i] + ["Y = 2"] + lines[i:]
    ast.parse("\n".join(out))                      # the insertion is outside the parentheses
    assert lines[i - 1].strip() == ")"


def test_no_imports_still_returns_zero():
    assert _last_import_idx(["x = 1"]) == 0


def test_overload_stubs_are_not_annotated():
    from dftracer_agents.mcp_tools.tools.session.annotation_python import _extract_functions_from_ast

    src = (
        "from typing import overload\n"
        "class T:\n"
        "    @overload\n"
        "    @staticmethod\n"
        "    def f(x: int) -> int: ...\n"
        "    @overload\n"
        "    @staticmethod\n"
        "    def f(x: str) -> str: ...\n"
        "    @staticmethod\n"
        "    def f(x):\n"
        "        return x\n"
    )
    names = [(fn["name"], fn["start_line"]) for fn in _extract_functions_from_ast(src)]
    assert names == [("f", 10)], names


def test_classmethod_gets_a_region_not_a_decorator():
    from dftracer_agents.mcp_tools.tools.session.annotation_python import _extract_functions_from_ast

    src = (
        "class T:\n"
        "    @classmethod\n"
        "    def make(cls, x):\n"
        "        return cls\n"
    )
    fn = _extract_functions_from_ast(src)[0]
    assert fn["has_staticmethod"] is True          # the flag that selects the contextual region
