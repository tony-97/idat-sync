import ast
from pathlib import Path
import unittest


def _iter_module_imports(py_file: Path) -> list[tuple[int, str]]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
    return imports


def _matches_prefix(name: str, prefixes: tuple[str, ...]) -> bool:
    return any(name == p or name.startswith(f"{p}.") for p in prefixes)


class TestArchitectureBoundaries(unittest.TestCase):
    def test_no_requests_imports_across_src(self):
        src_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync"
        violations = [
            f"{py_file.relative_to(src_root).as_posix()}:{lineno} imports {name}"
            for py_file in src_root.rglob("*.py")
            for lineno, name in _iter_module_imports(py_file)
            if _matches_prefix(name, ("requests",))
        ]
        self.assertEqual(violations, [], f"Forbidden requests imports: {violations}")

    def test_no_httpx_imports_in_domain_or_application(self):
        src_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync"
        violations = [
            f"{py_file.relative_to(src_root).as_posix()}:{lineno} imports {name}"
            for target_dir in (src_root / "domain", src_root / "application")
            for py_file in target_dir.rglob("*.py")
            for lineno, name in _iter_module_imports(py_file)
            if _matches_prefix(name, ("httpx",))
        ]
        self.assertEqual(violations, [], f"Forbidden httpx imports: {violations}")

    def test_task_orchestrator_has_no_events_or_threading_imports(self):
        src_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync"
        orchestrator_path = src_root / "application" / "task_orchestrator.py"
        violations = [
            f"task_orchestrator.py:{lineno} imports {name}"
            for lineno, name in _iter_module_imports(orchestrator_path)
            if _matches_prefix(name, ("idat_sync.application.events", "threading"))
        ]
        self.assertEqual(
            violations,
            [],
            f"task_orchestrator.py must not import events or threading: {violations}",
        )


if __name__ == "__main__":
    unittest.main()
