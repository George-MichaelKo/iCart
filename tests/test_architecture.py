"""Protect dependency direction as the project grows."""

import ast
from pathlib import Path
import unittest


class ArchitectureTests(unittest.TestCase):
    def test_inner_layers_do_not_import_outer_layers(self):
        root = Path(__file__).resolve().parents[1] / "src" / "icart"
        forbidden = {
            "domain": ("icart.application", "icart.adapters", "icart.interfaces", "icart.bootstrap",
                       "httpx", "requests", "fastapi", "langgraph", "anthropic", "openai"),
            "application": ("icart.adapters", "icart.interfaces", "icart.bootstrap"),
            "adapters": ("icart.interfaces", "icart.bootstrap"),
        }
        for layer, prefixes in forbidden.items():
            for file in (root / layer).glob("*.py"):
                tree = ast.parse(file.read_text())
                for node in ast.walk(tree):
                    imports = ([node.module or ""] if isinstance(node, ast.ImportFrom) else
                               [n.name for n in node.names] if isinstance(node, ast.Import) else [])
                    for imported in imports:
                        self.assertFalse(any(imported == p or imported.startswith(p + ".")
                                             for p in prefixes), f"{file}: forbidden import {imported}")
