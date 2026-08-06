import pytest
from app.security import validate_public_url, is_private_host, make_token, read_token


def test_public_url():
    assert validate_public_url('https://example.com/video') == 'https://example.com/video'


def test_blocks_localhost():
    with pytest.raises(ValueError):
        validate_public_url('http://127.0.0.1/video')


def test_blocks_credentials():
    with pytest.raises(ValueError):
        validate_public_url('https://u:p@example.com/video')


def test_blocks_unresolvable_host():
    """كان gaierror:pass يقبل أي اسم لا يُحل — الآن مرفوض (ثغرة SSRF)."""
    with pytest.raises(ValueError):
        validate_public_url('http://nonexistent-domain-xyz-12345.invalid/video')


def test_blocks_cgnat_shared():
    """100.64.0.0/10 (CGNAT) كان يتجاوز الفحص — الآن مرفوض."""
    with pytest.raises(ValueError):
        validate_public_url('http://100.64.0.1/video')


def test_is_private_host_heuristic():
    assert is_private_host('http://172.19.0.4:8000/') is True       # شبكة docker الداخلية
    assert is_private_host('http://redis:6379/') is True             # اسم خدمة داخلي لا يُحل
    assert is_private_host('http://169.254.169.254/latest') is True  # metadata السحابية
    assert is_private_host('https://example.com/v.mp4') is False


def test_token_roundtrip():
    t = make_token('job-abc')
    assert read_token(t) == 'job-abc'


def test_token_tamper_rejected():
    t = make_token('job-abc')
    with pytest.raises(Exception):
        read_token(t[:-6] + 'XXXXXX')
