from pathlib import Path


def _requirements(path: str) -> list[str]:
    return [
        line.strip()
        for line in Path(path).read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_runtime_dependencies_exclude_test_tools() -> None:
    runtime = _requirements("requirements-runtime.txt")
    lowered = [line.lower() for line in runtime]

    assert not any(line.startswith("pytest") for line in lowered)
    assert not any(line.startswith("pip-audit") for line in lowered)
    assert "fastapi==0.139.2" in lowered
    assert "yt-dlp==2026.7.23.234303.dev0" in lowered


def test_development_dependencies_extend_runtime() -> None:
    development = _requirements("requirements-dev.txt")

    assert "-r requirements-runtime.txt" in development
    assert any(line.startswith("pytest==") for line in development)
    assert any(line.startswith("pytest-asyncio==") for line in development)


def test_compatibility_requirements_install_development_tooling() -> None:
    assert _requirements("requirements.txt") == ["-r requirements-dev.txt"]


def test_dockerfile_uses_test_and_runtime_stages_without_copying_repo_to_final() -> None:
    dockerfile = Path("Dockerfile").read_text(encoding="utf-8")
    lowered = dockerfile.lower()

    assert lowered.count("from ") >= 3
    assert " as test" in lowered
    assert " as runtime" in lowered
    assert "requirements-runtime.txt" in dockerfile
    assert "requirements-dev.txt" in dockerfile
    assert "python -m pytest" in lowered

    runtime_section = lowered.rsplit(" as runtime", 1)[-1]
    assert "copy . ." not in runtime_section
    assert "user app" in runtime_section


def test_docker_context_excludes_local_state_and_repository_metadata() -> None:
    ignored = set(_requirements(".dockerignore"))

    assert {".git", ".env", "data", "storage", "docs", "__pycache__"} <= ignored
