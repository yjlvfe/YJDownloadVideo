import ipaddress, socket, secrets
from urllib.parse import urlparse
from itsdangerous import URLSafeTimedSerializer, BadSignature
from .config import settings

serializer = URLSafeTimedSerializer(settings.secret_key, salt="yjdownload-file")

# نطاقات خاصة إضافية لا يغطيها ipaddress في كل الإصدارات (CGNAT 100.64/10)
_EXTRA_PRIVATE_NETS = [ipaddress.ip_network("100.64.0.0/10")]


def _addr_is_internal(addr) -> bool:
    if (addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved):
        return True
    for net in _EXTRA_PRIVATE_NETS:
        if addr in net:
            return True
    return False


def _host_is_internal(host: str) -> bool:
    """True إذا كان المضيف عنصرًا داخليًا (خاص/حلقي/رابط محلي/مشارك CGNAT).

    يرفض أيضًا الأسماء غير القابلة للحل — القبول الصامت كان ثغرة SSRF
    (الاسم لا يُحل وقت الفحص لكنه قد يُحل لعنوان داخلي وقت التنفيذ).
    """
    h = host.lower()
    if h in {"localhost", "127.0.0.1", "0.0.0.0", "::1"} or h.endswith(".local"):
        return True
    try:
        infos = socket.getaddrinfo(h, None)
    except socket.gaierror:
        return True  # لا يُحل إطلاقًا — مرفوض
    except Exception:  # noqa: BLE001
        return True
    for i in infos:
        try:
            addr = ipaddress.ip_address(i[4][0])
        except ValueError:
            continue
        if _addr_is_internal(addr):
            return True
    return False


def is_private_host(url: str) -> bool:
    """True إذا كان مضيف الرابط (مهما كان مساره) عنوانًا داخليًا."""
    try:
        p = urlparse(url)
    except Exception:  # noqa: BLE001
        return True
    host = p.hostname
    if not host:
        return True
    return _host_is_internal(host)


def validate_public_url(raw: str) -> str:
    p = urlparse(raw.strip())
    if p.scheme not in {"http", "https"} or not p.hostname or p.username or p.password:
        raise ValueError("الرابط غير صالح")
    if _host_is_internal(p.hostname):
        raise ValueError("لا يسمح بعنوان داخلي")
    return raw.strip()


def make_token(job_id: str) -> str:
    return serializer.dumps({"job_id": job_id, "nonce": secrets.token_urlsafe(12)})


def read_token(token: str) -> str:
    # Accept permanent token or serialized token
    try:
        data = serializer.loads(token, max_age=315360000)
        return data["job_id"]
    except Exception:
        return token
