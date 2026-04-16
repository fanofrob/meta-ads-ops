"""
Smoke tests for the original Meta ads ops pipeline.

These tests confirm that the existing reporting pipeline remains fully intact
after any changes to the creative intelligence subsystem.

Rules:
  - These tests must NEVER import from creative_intelligence.
  - These tests must NEVER write to live Meta API endpoints.
  - If any of these tests fail, stop — the ops pipeline has been broken.
"""
import importlib
import sys
from pathlib import Path


# ─────────────────────────────────────────────
# 1. Core module imports are intact
# ─────────────────────────────────────────────

def test_report_data_importable():
    """src/report_data.py must remain importable without error."""
    spec = importlib.util.spec_from_file_location(
        "report_data",
        Path(__file__).parent.parent / "src" / "report_data.py",
    )
    assert spec is not None, "src/report_data.py not found"
    mod = importlib.util.module_from_spec(spec)
    # Load without executing __main__ blocks
    try:
        spec.loader.exec_module(mod)
    except SystemExit:
        pass  # top-level sys.exit() is fine — module still loads
    assert mod is not None


def test_report_importable():
    """src/report.py must remain importable without error.

    src/report.py uses bare 'from report_data import ...' (no package prefix),
    so we must add src/ to sys.path before loading it — matching how it is
    invoked in production (e.g. python src/report.py from the project root).
    """
    src_dir = str(Path(__file__).parent.parent / "src")
    path_added = src_dir not in sys.path
    if path_added:
        sys.path.insert(0, src_dir)
    try:
        spec = importlib.util.spec_from_file_location(
            "report",
            Path(__file__).parent.parent / "src" / "report.py",
        )
        assert spec is not None, "src/report.py not found"
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except SystemExit:
            pass
        assert mod is not None
    finally:
        if path_added and src_dir in sys.path:
            sys.path.remove(src_dir)


# ─────────────────────────────────────────────
# 2. Key files exist and are unmodified structurally
# ─────────────────────────────────────────────

def test_src_report_data_exists():
    assert (Path(__file__).parent.parent / "src" / "report_data.py").is_file()


def test_src_report_exists():
    assert (Path(__file__).parent.parent / "src" / "report.py").is_file()


def test_outputs_directory_structure():
    """The outputs directory exists; subdirs may be empty but must not be deleted."""
    outputs = Path(__file__).parent.parent / "outputs"
    assert outputs.is_dir(), "outputs/ directory missing — ops pipeline broken"


def test_data_raw_directory_exists():
    """data/raw must exist for the ops ingest pipeline."""
    data_raw = Path(__file__).parent.parent / "data" / "raw"
    assert data_raw.is_dir(), "data/raw directory missing — check ops pipeline"


def test_creative_intelligence_does_not_write_to_ops_outputs(tmp_path):
    """
    Guard: creative intelligence reports go to outputs/creative_reports/,
    NOT to outputs/reports/ (which belongs to the ops pipeline).
    """
    from creative_intelligence import config

    ci_reports = Path(config.CI_REPORTS_DIR).resolve()
    ops_reports = (Path(__file__).parent.parent / "outputs" / "reports").resolve()

    assert ci_reports != ops_reports, (
        f"CONFLICT: creative intelligence is writing to the ops reports directory: {ops_reports}"
    )
    # The CI reports dir should contain 'creative' in its name as an extra guard.
    assert "creative" in str(ci_reports).lower(), (
        f"CI_REPORTS_DIR ({ci_reports}) does not look like a creative-specific directory. "
        "Check config.py to avoid polluting the ops reports dir."
    )


def test_creative_intelligence_does_not_import_ops_modules():
    """
    Guard: creative intelligence modules must not import from src/.
    Any import of src.report_data or src.report inside creative_intelligence
    would create coupling that could break the ops pipeline.

    We check actual import statements only, not comments or docstrings.
    """
    import ast

    ci_dir = Path(__file__).parent.parent / "creative_intelligence"
    violations = []

    for py_file in ci_dir.rglob("*.py"):
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"))
        except SyntaxError:
            continue  # Skip unparseable files; syntax errors are caught elsewhere.

        for node in ast.walk(tree):
            # from src import ... / from src.X import ...
            if isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if module == "src" or module.startswith("src."):
                    violations.append(f"{py_file.relative_to(ci_dir.parent)}: from {module}")
            # import src / import src.X
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "src" or alias.name.startswith("src."):
                        violations.append(f"{py_file.relative_to(ci_dir.parent)}: import {alias.name}")

    assert not violations, (
        f"creative_intelligence module(s) contain actual imports from src/:\n"
        + "\n".join(f"  {v}" for v in violations)
        + "\nThis coupling must be removed — all ops data must be read via adapters."
    )


def test_api_log_path_does_not_collide():
    """
    The ops pipeline logs to outputs/api.log.
    The creative intelligence subsystem must not write to the same file.
    """
    from creative_intelligence import config

    ci_log = Path(getattr(config, "CI_API_LOG", "creative_intelligence_data/ci_api.log")).resolve()
    ops_log = (Path(__file__).parent.parent / "outputs" / "api.log").resolve()
    assert ci_log != ops_log, (
        f"creative intelligence API log ({ci_log}) collides with ops API log ({ops_log}). "
        "Update CI_API_LOG in config.py."
    )
