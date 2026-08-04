from types import SimpleNamespace
from app.media_diagnostics import classify_media_error, recent_media_failures

def test_media_error_classification_is_specific():
    assert classify_media_error('Requested format is not available') == 'format_unavailable'
    assert classify_media_error('youtube_bot_check') == 'youtube_bot_check'
    assert classify_media_error('HTTP Error 429') == 'rate_limited'
    assert classify_media_error('database is locked') == 'database_busy'
    assert classify_media_error('المقطع المتاح بدون صوت') == 'missing_audio'

def test_recent_failures_are_sanitized():
    jobs=[SimpleNamespace(status='failed',requested_format='720',error='ERROR https://cdn.example/file?sig=secret Requested format is not available',id='secret',url='https://private',title='private',owner_key='owner')]
    result=recent_media_failures(jobs)
    assert result[0]['code']=='format_unavailable'
    rendered=repr(result)
    assert 'sig=secret' not in rendered and 'secret' not in rendered.replace('[REDACTED_QUERY]','')
