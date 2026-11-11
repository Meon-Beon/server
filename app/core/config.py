import os

from dotenv import load_dotenv
from sqlalchemy.engine import URL

# server/.env 파일의 값을 환경변수로 읽어온다
load_dotenv()

# URL.create를 쓰면 비밀번호에 @, # 같은 특수문자가 있어도 안전하게 처리된다
DATABASE_URL = URL.create(
    drivername="mysql+pymysql",
    username=os.getenv("DB_USER", "root"),
    password=os.getenv("DB_PASSWORD", ""),
    host=os.getenv("DB_HOST", "localhost"),
    port=int(os.getenv("DB_PORT", "3306")),
    database=os.getenv("DB_NAME", "gene_insurance"),
    query={"charset": "utf8mb4"},
)
