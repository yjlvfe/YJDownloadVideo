from pathlib import Path


def test_repository_uses_one_release_gate_workflow() -> None:
    workflow = Path(".github/workflows/vercel-media-flow.yml")

    assert workflow.is_file()
    assert not Path(".github/workflows/tests.yml").exists()


def test_release_gate_covers_main_fixes_audits_tests_and_both_docker_targets() -> None:
    text = Path(".github/workflows/vercel-media-flow.yml").read_text(encoding="utf-8")

    assert "pull_request:" in text
    assert "push:" in text
    assert "- main" in text
    assert "fix/**" in text
    assert "pip-audit -r requirements-runtime.txt" in text
    assert "python scripts/check_tracked_secrets.py" in text
    assert "PYTHONPATH=. pytest -q" in text
    assert "python -m compileall -q app scripts" in text
    assert "docker build --target test" in text
    assert "docker build --target runtime" in text


def test_secret_scan_is_a_reusable_script_that_never_prints_values() -> None:
    script = Path("scripts/check_tracked_secrets.py")

    assert script.is_file()
    text = script.read_text(encoding="utf-8")
    assert "git" in text and "ls-files" in text
    assert "Values and matching lines are intentionally not displayed" in text
    assert "match.group" not in text
