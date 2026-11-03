"""Extension and application code must not import the graph runtime directly."""

import ast
from pathlib import Path


def test_graph_imports_stay_inside_kernel():
    root = Path(__file__).resolve().parents[3]
    violations = []
    for directory in ("core", "capabilities", "projects", "api", "tests"):
        for path in (root / directory).rglob("*.py"):
            if path.is_relative_to(root / "core" / "kernel"):
                continue
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
                modules = []
                if isinstance(node, ast.Import):
                    modules = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom):
                    modules = [node.module or ""]
                elif (
                    isinstance(node, ast.Call)
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)
                    and (
                        isinstance(node.func, ast.Name)
                        and node.func.id == "__import__"
                        or isinstance(node.func, ast.Attribute)
                        and node.func.attr == "import_module"
                    )
                ):
                    modules = [node.args[0].value]
                if any(name == "langgraph" or name.startswith("langgraph.") for name in modules):
                    violations.append(f"{path.relative_to(root)}:{node.lineno}")
    assert violations == []
