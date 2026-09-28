"""SQLite 连接：WAL 模式支持 Streamlit 读 + 后台流水线写。"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from .config import settings
from .models import Base

engine = create_engine(settings.db_url, connect_args={"check_same_thread": False})


@event.listens_for(engine, "connect")
def _set_wal(dbapi_conn, _):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.close()


SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def init_db() -> None:
    Base.metadata.create_all(engine)
    # 轻量迁移：为已有表补充新列（SQLite 不支持自动加列）
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(listing_tasks)")}
        if "image_url" not in cols:
            conn.exec_driver_sql("ALTER TABLE listing_tasks ADD COLUMN image_url VARCHAR(1024)")
        if "ozon_task_id" not in cols:
            conn.exec_driver_sql("ALTER TABLE listing_tasks ADD COLUMN ozon_task_id VARCHAR(128)")
        if "ozon_status" not in cols:
            conn.exec_driver_sql("ALTER TABLE listing_tasks ADD COLUMN ozon_status VARCHAR(32)")
        conn.commit()
