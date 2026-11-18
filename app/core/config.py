import os
import secrets

from dotenv import load_dotenv
from sqlalchemy.engine import URL

# server/.env 파일의 값을 환경변수로 읽어온다
load_dotenv()

def _database_url():
    """DATABASE_URL 이 있으면 그 주소(배포: Supabase PostgreSQL), 없으면 DB_* 값으로 로컬 MySQL"""
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        # Supabase 가 주는 postgres:// · postgresql:// 주소를 SQLAlchemy 의 psycopg(3) 드라이버용으로 바꿈
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url
    # URL.create를 쓰면 비밀번호에 @, # 같은 특수문자가 있어도 안전하게 처리된다
    return URL.create(
        drivername="mysql+pymysql",
        username=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASSWORD", ""),
        host=os.getenv("DB_HOST", "localhost"),
        port=int(os.getenv("DB_PORT", "3306")),
        database=os.getenv("DB_NAME", "gene_insurance"),
        query={"charset": "utf8mb4"},
    )


DATABASE_URL = _database_url()

# 프론트 주소 (CORS 허용 목록). 배포하면 Vercel 프론트 주소를 쉼표로 이어서 넣는다
# 예) CORS_ORIGINS=http://localhost:5173,https://gene-xxx.vercel.app
CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if o.strip()]

# 토스페이먼츠 시크릿 키 (결제 승인 API용, 서버에서만 사용)
# .env에 TOSS_SECRET_KEY가 없으면 토스 문서 공개용 테스트 키를 쓴다.
# 프론트의 클라이언트 키(test_gck_docs_...)와 같은 세트여야 승인이 된다
TOSS_SECRET_KEY = os.getenv("TOSS_SECRET_KEY", "test_gsk_docs_OaPz8L5KdmQXkzRz3y47BMw6")

# ---------- 카카오 로그인 ----------
# Kakao Developers > 내 애플리케이션 > [앱] > [플랫폼 키] > REST API 키
KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
# [보안] 메뉴에서 클라이언트 시크릿을 켰을 때만 필요 (끄면 비워 둠)
KAKAO_CLIENT_SECRET = os.getenv("KAKAO_CLIENT_SECRET", "")
# 카카오 앱에 등록한 리다이렉트 URI 와 글자 하나까지 같아야 한다
KAKAO_REDIRECT_URI = os.getenv("KAKAO_REDIRECT_URI", "http://localhost:5173/oauth/kakao")
# 시연용: login 이면 브라우저에 카카오 로그인 상태가 남아 있어도 매번 카카오 로그인 화면(아이디·비밀번호)을 보여 준다.
# 끄려면 .env 에 KAKAO_PROMPT= (빈 값)
KAKAO_PROMPT = os.getenv("KAKAO_PROMPT", "login")
# 카카오 첫 로그인 → 추가 정보 입력 사이에 쓰는 "가입 티켓" 서명용 비밀값.
# .env 에 없으면 서버를 켤 때마다 새로 만든다 (서버를 재시작하면 입력 중이던 티켓만 무효가 됨)
AUTH_SECRET = os.getenv("AUTH_SECRET") or secrets.token_hex(32)
