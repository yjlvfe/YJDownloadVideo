"""اختبارات قارئ تقدم الرفع (ProgressHTTPXRequest)."""
import io

from app.progress_request import _ProgressReader


def test_reader_reports_10_percent_steps():
    data = b"x" * 1000
    steps = []

    def cb(current, total):
        steps.append((current, total))

    reader = _ProgressReader(io.BytesIO(data), len(data), cb)
    while reader.read(100):  # 10 قراءات × 10% بالضبط
        pass

    pcts = [round(cur / total * 100) for cur, total in steps]
    assert pcts == [10, 20, 30, 40, 50, 60, 70, 80, 90, 100]


def test_reader_delegates_seek_and_tell():
    bio = io.BytesIO(b"abcdef")
    reader = _ProgressReader(bio, 6, None)
    assert reader.read(2) == b"ab"
    assert reader.tell() == 2
    reader.seek(0)
    assert reader.tell() == 0
