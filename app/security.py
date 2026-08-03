import ipaddress, socket, secrets
from urllib.parse import urlparse
from itsdangerous import URLSafeTimedSerializer, BadSignature
from .config import settings

serializer = URLSafeTimedSerializer(settings.secret_key, salt="yjdownload-file")

def validate_public_url(raw: str) -> str:
    p=urlparse(raw.strip())
    if p.scheme not in {"http","https"} or not p.hostname or p.username or p.password:
        raise ValueError("الرابط غير صالح")
    host=p.hostname.lower()
    if host in {"localhost","127.0.0.1","0.0.0.0","::1"} or host.endswith(".local"):
        raise ValueError("لا يسمح بروابط الشبكات الداخلية")
    try:
        infos=socket.getaddrinfo(host, None)
        for i in infos:
            addr=ipaddress.ip_address(i[4][0])
            if addr.is_private or addr.is_loopback or addr.is_link_local or addr.is_reserved:
                raise ValueError("لا يسمح بعنوان داخلي")
    except socket.gaierror: pass
    return raw.strip()

def make_token(job_id: str) -> str: return serializer.dumps({"job_id": job_id, "nonce": secrets.token_urlsafe(12)})
def read_token(token: str) -> str:
    data=serializer.loads(token, max_age=settings.file_ttl_seconds)
    return data["job_id"]
