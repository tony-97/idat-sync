"""Architectural boundary tests verifying layer import rules."""

import ast
import importlib.metadata
import json
import mimetypes
import os
import re
import tempfile
import tomllib
import unittest
from pathlib import Path

FORBIDDEN_INNER_LAYER_IMPORTS: tuple[str, ...] = (
    "idat_sync.config",
    "idat_sync.infrastructure",
    "idat_sync.ui",
    "requests",
    "httpx",
    "office365",
    "yt_dlp",
    "playwright",
    "tkinter",
    "async_tkinter_loop",
    "webview",
    "jnius",
)


def _dynamic_import_target(node: ast.AST) -> str | None:
    if not (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "import_module"
        and node.args
    ):
        return None
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    return None


def iter_module_imports(py_file: Path) -> list[tuple[int, str]]:
    """Return (lineno, module_name) pairs from static and import_module() imports."""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend((node.lineno, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
        elif (target := _dynamic_import_target(node)) is not None:
            imports.append((node.lineno, target))
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
        """Ensure task_orchestrator.py stays decoupled from events and threading."""
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

    def test_exceptions_and_ports_have_zero_vendor_or_outer_layer_imports(self):
        """Ensure application.exceptions and ports avoid outer layers and SDKs."""
        src_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync"
        exceptions_path = src_root / "application" / "exceptions.py"
        ports_path = src_root / "application" / "ports.py"
        assert exceptions_path.is_file(), "application/exceptions.py must exist"
        violations = [
            f"{py_file.name}:{lineno} imports {name}"
            for py_file in (exceptions_path, ports_path)
            for lineno, name in iter_module_imports(py_file)
            if matches_prefix(name, FORBIDDEN_INNER_LAYER_IMPORTS)
        ]
        assert violations == [], f"Forbidden imports in exceptions/ports: {violations}"

    def test_domain_and_application_have_zero_forbidden_imports(self):
        """Ensure domain and application layers avoid outer layers and vendor SDKs."""
        src_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync"
        violations = [
            f"{py_file.relative_to(src_root).as_posix()}:{lineno} imports {name}"
            for target_dir in (src_root / "domain", src_root / "application")
            for py_file in target_dir.rglob("*.py")
            for lineno, name in iter_module_imports(py_file)
            if matches_prefix(name, FORBIDDEN_INNER_LAYER_IMPORTS)
        ]
        assert violations == [], (
            f"Forbidden imports in domain/application: {violations}"
        )

    def test_no_forbidden_legacy_imports_or_print_calls_across_src(self):
        """Ensure src/idat_sync never imports removed frameworks or calls print()."""
        src_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync"
        forbidden = ("playwright", "tkinter", "async_tkinter_loop", "requests")
        import_violations = [
            f"{py_file.relative_to(src_root).as_posix()}:{lineno} imports {name}"
            for py_file in src_root.rglob("*.py")
            for lineno, name in iter_module_imports(py_file)
            if matches_prefix(name, forbidden)
        ]
        assert import_violations == [], (
            f"Forbidden imports across src/idat_sync: {import_violations}"
        )

        print_violations = [
            f"{py_file.relative_to(src_root).as_posix()}:{node.lineno} calls print()"
            for py_file in src_root.rglob("*.py")
            for node in ast.walk(
                ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
            )
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]
        assert print_violations == [], (
            f"Forbidden print() calls across src/idat_sync: {print_violations}"
        )

    def test_ui_never_imports_infrastructure(self):
        """Ensure src/idat_sync/ui never imports idat_sync.infrastructure."""
        src_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync"
        violations = [
            f"{py_file.relative_to(src_root).as_posix()}:{lineno} imports {name}"
            for py_file in (src_root / "ui").rglob("*.py")
            for lineno, name in iter_module_imports(py_file)
            if matches_prefix(name, ("idat_sync.infrastructure",))
        ]
        assert violations == [], f"Forbidden infrastructure imports in ui: {violations}"

    def test_webview_isolated_to_app_and_sharepoint_auth(self):
        """Ensure webview imports are confined to ui/app.py and sharepoint_auth.py."""
        src_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync"
        allowed = {"ui/app.py", "infrastructure/sharepoint_auth.py"}
        violations = [
            f"{py_file.relative_to(src_root).as_posix()}:{lineno} imports {name}"
            for py_file in src_root.rglob("*.py")
            if py_file.relative_to(src_root).as_posix() not in allowed
            for lineno, name in iter_module_imports(py_file)
            if matches_prefix(name, ("webview",))
        ]
        assert not violations, f"webview imported outside allowed modules: {violations}"

    def test_layer_init_files_have_zero_imports(self):
        """Ensure layer __init__.py files contain only module docstrings, no imports."""
        src_root = Path(__file__).resolve().parent.parent / "src" / "idat_sync"
        violations: list[str] = []
        for layer in ("application", "domain", "infrastructure"):
            init_file = src_root / layer / "__init__.py"
            tree = ast.parse(
                init_file.read_text(encoding="utf-8"), filename=str(init_file)
            )
            is_docstring_only = (
                len(tree.body) == 1
                and isinstance(tree.body[0], ast.Expr)
                and isinstance(tree.body[0].value, ast.Constant)
                and isinstance(tree.body[0].value.value, str)
            )
            if not is_docstring_only:
                violations.append(f"{layer}/__init__.py")
        assert not violations, (
            f"Layer __init__.py files must contain only a docstring: {violations}"
        )

    def test_colab_notebook_setup_cell_commands(self):
        """Verify colab.ipynb cell 1 installs weasyprint via pip and apt uses -y."""
        project_root = Path(__file__).resolve().parent.parent
        notebook = json.loads(
            (project_root / "colab.ipynb").read_text(encoding="utf-8")
        )
        code_cells = [c for c in notebook["cells"] if c.get("cell_type") == "code"]
        cell1_lines = [line.strip() for line in code_cells[0]["source"]]
        pip_lines = [line for line in cell1_lines if line.startswith("%pip install")]
        assert pip_lines
        assert "weasyprint" in pip_lines[0]
        assert not any("apt" in line and "weasyprint" in line for line in cell1_lines)
        apt_install_lines = [
            line
            for line in cell1_lines
            if re.search(r"\bapt(?:-get)?\s+install\b", line)
        ]
        assert apt_install_lines
        for line in apt_install_lines:
            assert " -y " in line or line.endswith(" -y"), f"Missing -y: {line}"

    def test_colab_notebook_two_pass_postprocessing(self):  # pylint: disable=too-many-locals
        """Verify colab.ipynb cell 3 updates stale outputs and deduplicates links."""
        project_root = Path(__file__).resolve().parent.parent
        notebook = json.loads(
            (project_root / "colab.ipynb").read_text(encoding="utf-8")
        )
        code_cells = [c for c in notebook["cells"] if c.get("cell_type") == "code"]
        cell3_source = "".join(code_cells[2]["source"])
        tree = ast.parse(cell3_source, filename="colab.ipynb:cell3")
        fn_node = next(
            (
                node
                for node in tree.body
                if isinstance(node, ast.FunctionDef)
                and node.name == "postprocess_course_folder"
            ),
            None,
        )
        assert fn_node is not None, (
            "colab.ipynb cell 3 must define postprocess_course_folder"
        )
        converted_html: list[str] = []
        converted_video: list[str] = []

        def _fake_html_to_pdf(path: str) -> str:
            converted_html.append(Path(path).name)
            out = Path(path).with_suffix(".pdf")
            out.write_bytes(b"new-pdf")
            return str(out)

        def _fake_video_to_mp3(path: str) -> str:
            converted_video.append(Path(path).name)
            out = Path(path).with_suffix(".mp3")
            out.write_bytes(b"new-mp3")
            return str(out)

        namespace: dict[str, object] = {
            "os": os,
            "mimetypes": mimetypes,
            "html_to_pdf": _fake_html_to_pdf,
            "video_to_mp3": _fake_video_to_mp3,
            "get_share_link": lambda p: f"link:{Path(p).name}",
        }
        exec(  # noqa: S102  # pylint: disable=exec-used
            compile(ast.Module(body=[fn_node], type_ignores=[]), "colab", "exec"),
            namespace,
        )
        postprocess = namespace["postprocess_course_folder"]
        assert callable(postprocess)

        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            mtimes = {
                "stale.pdf": 100,
                "stale.html": 200,
                "fresh.html": 100,
                "fresh.pdf": 200,
                "stale.mp3": 100,
                "stale.mp4": 200,
                "fresh.mp4": 100,
                "fresh.mp3": 200,
                "notes.txt": 100,
            }
            for name, ts in mtimes.items():
                p = root / name
                p.write_bytes(b"x")
                os.utime(p, (ts, ts))

            recordings, links = postprocess(str(root))

        assert converted_html == ["stale.html"]
        assert converted_video == ["stale.mp4"]
        assert sorted(recordings) == ["link:fresh.mp3", "link:stale.mp3"]
        assert sorted(links) == ["link:fresh.pdf", "link:notes.txt", "link:stale.pdf"]

    def test_ci_workflow_gates_builds_behind_check_job(self):
        """Verify build_and_deploy.yml triggers, check job, and build gating."""
        project_root = Path(__file__).resolve().parent.parent
        workflow = (
            project_root / ".github" / "workflows" / "build_and_deploy.yml"
        ).read_text(encoding="utf-8")
        assert "pull_request:" in workflow
        assert "branches: [main]" in workflow or "- main" in workflow
        assert "workflow_dispatch:" in workflow
        assert re.search(r"(?m)^  check:\s*$", workflow)
        assert "python -m unittest" in workflow
        assert "ruff check ." in workflow
        assert "pylint src tests" in workflow
        expected_if = (
            "if: github.ref_type == 'tag' || github.event_name == 'workflow_dispatch'"
        )
        assert workflow.count("needs: check") >= 2
        assert workflow.count(expected_if) >= 2

    def test_tests_have_zero_pytest_imports(self):
        """Ensure all tests/*.py use stdlib unittest without importing pytest."""
        tests_root = Path(__file__).resolve().parent
        violations = [
            f"{py_file.name}:{lineno} imports {name}"
            for py_file in sorted(tests_root.glob("*.py"))
            for lineno, name in iter_module_imports(py_file)
            if matches_prefix(name, ("pytest",))
        ]
        assert not violations, f"Forbidden pytest imports in tests/: {violations}"

    def test_buildozer_bundles_pyproject_dependencies(self):
        """Ensure buildozer.spec lists pyproject.toml deps and pywebview's pure deps."""
        project_root = Path(__file__).resolve().parent.parent

        def _norm(raw: str) -> str:
            name = re.split(r"[\s\[<>=!~;]", raw.strip(), maxsplit=1)[0]
            return re.sub(r"[-_.]+", "-", name).lower()

        spec_lines = (project_root / "buildozer.spec").read_text(encoding="utf-8")
        req_line = next(
            line
            for line in spec_lines.splitlines()
            if line.startswith("requirements =")
        )
        bundled = {_norm(item) for item in req_line.split("=", 1)[1].split(",")}

        pyproject = tomllib.loads(
            (project_root / "pyproject.toml").read_text(encoding="utf-8")
        )
        required = {
            _norm(dep)
            for dep in pyproject["project"]["dependencies"]
            if "darwin" not in dep
        }
        required.update(
            _norm(dep)
            for dep in (importlib.metadata.requires("pywebview") or [])
            if ";" not in dep
        )
        missing = sorted(required - bundled)
        assert not missing, f"Missing from buildozer.spec requirements: {missing}"


if __name__ == "__main__":
    unittest.main()
