"""بيئة اختبار معزولة: قاعدة/تخزين/حالة مؤقتة — لا تمس بيانات المشروع الحية أبدًا.

يُحمَّل قبل أي استيراد لـ app.* — يضمن أن Settings تقرأ المسارات المؤقتة.
"""
import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="yjtest-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["STORAGE_DIR"] = f"{_tmp}/storage"
os.environ["STATUS_DIR"] = f"{_tmp}/status"
os.environ["COOKIES_DIR"] = f"{_tmp}/cookies"
os.environ["SECRET_KEY"] = "test-secret-key-for-tests-only-1234567890"
os.environ["FILE_TTL_SECONDS"] = "86400"
os.environ["TELEGRAM_BOT_TOKEN"] = "123456789:TESTTOKEN"
os.environ["TELEGRAM_SEND_LIMIT_BYTES"] = "2097152000"
