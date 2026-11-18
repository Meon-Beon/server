from sqlalchemy import Connection, TextClause, create_engine, text
from sqlalchemy.pool import NullPool

from app.core.config import DATABASE_URL

# engine: DB 연결을 만들어 주는 공장. 서버 전체에서 하나만 만든다
if str(DATABASE_URL).startswith("postgresql"):
    # 배포(Vercel + Supabase): 요청마다 짧게 켜졌다 꺼지는 서버리스 함수라 연결을 들고 있지 않는다(NullPool).
    # Supabase 연결 풀러(트랜잭션 모드, 6543 포트)가 대신 연결을 모아 관리한다.
    # 트랜잭션 모드에서는 "준비된 문장(prepared statement)"을 쓸 수 없어서 prepare_threshold=None
    engine = create_engine(DATABASE_URL, poolclass=NullPool, connect_args={"prepare_threshold": None})
else:
    # 로컬 MySQL
    engine = create_engine(DATABASE_URL, pool_pre_ping=True)


def get_db():
    """요청 하나가 들어올 때 연결을 빌려주고, 응답이 끝나면 자동으로 반납한다."""
    with engine.connect() as conn:
        yield conn


def insert_returning_id(conn: Connection, statement: TextClause | str, params: dict) -> int:
    """INSERT 하고 새로 생긴 행의 id 를 돌려준다.

    MySQL 은 실행 결과의 lastrowid 로 알 수 있지만, PostgreSQL 은 INSERT ... RETURNING id 로 받아야 한다
    """
    sql = statement.text if isinstance(statement, TextClause) else statement
    if conn.dialect.name == "postgresql":
        return conn.execute(text(sql.rstrip().rstrip(";") + " RETURNING id"), params).scalar_one()
    return conn.execute(text(sql), params).lastrowid
