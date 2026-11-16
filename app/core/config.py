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

# 토스페이먼츠 시크릿 키 (결제 승인 API용, 서버에서만 사용)
# .env에 TOSS_SECRET_KEY가 없으면 토스 문서 공개용 테스트 키를 쓴다.
# 프론트의 클라이언트 키(test_gck_docs_...)와 같은 세트여야 승인이 된다
TOSS_SECRET_KEY = os.getenv("TOSS_SECRET_KEY", "test_gsk_docs_OaPz8L5KdmQXkzRz3y47BMw6")
