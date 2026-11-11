from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection, text

from app.database import get_db

router = APIRouter(prefix="/api", tags=["auth"])


# 요청 스키마: 프론트가 보내는 JSON의 모양
class LoginRequest(BaseModel):
    login_id: str
    password: str


# 응답 스키마: 비밀번호는 절대 포함하지 않는다
class LoginResponse(BaseModel):
    id: int
    login_id: str
    name: str | None = None


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, db: Connection = Depends(get_db)):
    # :login_id, :password 는 자리표시자. 값을 문자열로 이어 붙이지 않고
    # 따로 넘겨야 SQL 인젝션을 막을 수 있다
    query = text(
        """
        SELECT id, login_id, name
        FROM users
        WHERE login_id = :login_id
          AND password = :password
        """
    )
    row = db.execute(
        query, {"login_id": body.login_id, "password": body.password}
    ).mappings().first()

    # 0행이면 로그인 실패 -> 401 Unauthorized
    if row is None:
        raise HTTPException(
            status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다."
        )

    # TODO: 프로토타입이라 비밀번호 평문 비교. 실제 구현에서는 bcrypt 해시 + JWT 발급
    return row
