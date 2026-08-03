from pathlib import Path


def test_runtime_config_has_no_remote_worker_or_file_retention():
    source = Path("app/config.py").read_text(encoding="utf-8")

    assert "remote_worker_url" not in source
    assert 'file_ttl_seconds: int = 0' in source
    assert 'max_file_size_bytes: int = 2_000_000_000' in source
    assert 'telegram_send_limit_bytes: int = 2_000_000_000' in source
    assert 'runtime_root = Path("/tmp/yjdownload")' in source
