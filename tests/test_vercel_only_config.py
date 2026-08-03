from pathlib import Path


def test_vercel_config_disables_remote_worker_and_retention():
    source = Path("app/config.py").read_text(encoding="utf-8")
    assert 'remote_worker_url: str = ""' in source
    assert "self.file_ttl_seconds = 0" in source
    assert "self.max_file_size_bytes = min(self.max_file_size_bytes, 49_000_000)" in source
    assert "self.telegram_send_limit_bytes = min(" in source
