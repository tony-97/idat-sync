"""Architectural boundary tests verifying layer import rules."""

import ast
import unittest
from pathlib import Path


def iter_module_imports(py_file: Path) -> list[tuple[int, str]]:
    """Return (lineno, module_name) pairs from AST Import and ImportFrom nodes."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
    return imports


def matches_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    """Return True if ``name`` equals or starts with any module prefix."""
    return any(name == p or name.startswith(f"{p}.") for p in prefixes)


class TestArchitectureBoundaries(unittest.TestCase):
    """Verify hexagonal layer import boundaries across src/idat_sync."""

    def test_no_requests_imports_across_src(self):
        """Ensure requests is never imported directly in src/idat_sync."""
        src_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync"
        violations = [
            f"{py_file.relative_to(src_root).as_posix()}:{lineno} imports {name}"
            for py_file in src_root.rglob("*.py")
            for lineno, name in iter_module_imports(py_file)
            if matches_prefix(name, ("requests",))
        ]
        assert violations == [], f"Forbidden requests imports: {violations}"

    def test_no_httpx_imports_in_domain_or_application(self):
        """Ensure domain and application layers never import httpx."""
        src_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync"
        violations = [
            f"{py_file.relative_to(src_root).as_posix()}:{lineno} imports {name}"
            for target_dir in (src_root / "domain", src_root / "application")
            for py_file in target_dir.rglob("*.py")
            for lineno, name in iter_module_imports(py_file)
            if matches_prefix(name, ("httpx",))
        ]
        assert violations == [], f"Forbidden httpx imports: {violations}"

    def test_task_orchestrator_has_no_events_or_threading_imports(self):
        """Ensure TaskOrchestrator stays decoupled from events and threading."""
        src_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync"
        orchestrator_path = src_root / "application" / "task_orchestrator.py"
        violations = [
            f"task_orchestrator.py:{lineno} imports {name}"
            for lineno, name in iter_module_imports(orchestrator_path)
            if matches_prefix(name, ("idat_sync.application.events", "threading"))
        ]
        assert violations == [], (
            f"task_orchestrator.py must not import events or threading: {violations}"
        )


if __name__ == "__main__":
    unittest.main()
