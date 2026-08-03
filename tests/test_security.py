import pytest
from app.security import validate_public_url

def test_public_url():
    assert validate_public_url('https://example.com/video') == 'https://example.com/video'

def test_blocks_localhost():
    with pytest.raises(ValueError): validate_public_url('http://127.0.0.1/video')

def test_blocks_credentials():
    with pytest.raises(ValueError): validate_public_url('https://u:p@example.com/video')
