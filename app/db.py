from datetime import datetime, timezone
from sqlalchemy import String, Integer, BigInteger, DateTime, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker
from .config import settings

class Base(DeclarativeBase): pass
class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    url: Mapped[str] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    thumbnail: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_format: Mapped[str] = mapped_column(String(64), default="best")
    status: Mapped[str] = mapped_column(String(20), default="waiting", index=True)
    file_path: Mapped[str | None] = mapped_column(Text, nullable=True)
    download_token: Mapped[str | None] = mapped_column(String(128), unique=True, nullable=True)
    file_size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    duration: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="web")
    owner_key: Mapped[str] = mapped_column(String(128), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

engine = create_engine(settings.database_url.replace("+aiosqlite", ""),
                       connect_args={"check_same_thread": False})

# 🛡️ 4 عمليات (web/worker/bot/cleanup) تشارك نفس ملف SQLite — WAL + مهلة قفل طويلة
# يمنعان "database is locked" تحت أي ضغط
@event.listens_for(engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=30000")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
def init_db(): Base.metadata.create_all(engine)

def utcnow(): return datetime.now(timezone.utc)

def get_job(job_id):
    with SessionLocal() as db: return db.get(Job, job_id)
