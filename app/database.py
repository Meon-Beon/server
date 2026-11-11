from sqlalchemy import create_engine

from app.core.config import DATABASE_URL

# engine: DB 연결을 만들어 주는 공장. 서버 전체에서 하나만 만든다
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def get_db():
    """요청 하나가 들어올 때 연결을 빌려주고, 응답이 끝나면 자동으로 반납한다."""
    with engine.connect() as conn:
        yield conn
