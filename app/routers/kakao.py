"""카카오 로그인 (REST API 방식, 인가 코드 → 토큰 → 사용자 정보)

흐름
1) GET  /api/auth/kakao/login-url?state=…  카카오 로그인 화면 주소를 만들어 준다 (REST API 키는 서버 .env 에만)
2) 사용자가 카카오에서 로그인·동의 → 카카오가 프론트 /oauth/kakao?code=…&state=… 로 돌려보냄
3) POST /api/auth/kakao/login {code}
   - 서버가 code 를 카카오 토큰으로 바꾸고(클라이언트 시크릿은 여기서만 씀) 카카오 회원번호·닉네임을 가져온다
   - 이미 가입한 카카오 회원이면 → {status: "ok", user}
   - 처음이면 → {status: "need_profile", ticket, nickname}  (아직 DB 에 만들지 않음)
4) POST /api/auth/kakao/signup {ticket, name, 주민번호 앞6·뒤1, phone}
   - 티켓 서명을 확인해 "카카오가 확인해 준 그 사람" 인지 검증한 뒤 회원을 만든다
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from app.core.config import (
    AUTH_SECRET,
    KAKAO_CLIENT_SECRET,
    KAKAO_PROMPT,
    KAKAO_REDIRECT_URI,
    KAKAO_REST_API_KEY,
)
from app.database import get_db, insert_returning_id
from app.routers.auth import PHONE_RULE, LoginResponse, parse_rrn
from app.services.demo_seed import seed_if_demo

router = APIRouter(prefix="/api/auth/kakao", tags=["kakao"])

AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
USER_URL = "https://kapi.kakao.com/v2/user/me"
TICKET_TTL = 10 * 60  # 추가 정보 입력까지 10분


def require_config() -> None:
    if not KAKAO_REST_API_KEY:
        raise HTTPException(
            status_code=503,
            detail="카카오 로그인 설정이 없습니다. server/.env 에 KAKAO_REST_API_KEY 를 넣어 주세요.",
        )


# ---------- 가입 티켓: 카카오가 확인해 준 회원번호를 위조할 수 없게 서버가 서명해 둔 값 ----------
# 형식: base64url(JSON) + "." + HMAC-SHA256 서명. 프론트가 내용을 바꾸면 서명이 맞지 않는다
def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _sign(body: str) -> str:
    return hmac.new(AUTH_SECRET.encode(), body.encode(), hashlib.sha256).hexdigest()


def make_ticket(kakao_id: int, nickname: str | None) -> str:
    body = _b64(json.dumps({"kid": kakao_id, "nick": nickname, "exp": int(time.time()) + TICKET_TTL}).encode())
    return f"{body}.{_sign(body)}"


def read_ticket(ticket: str) -> dict:
    try:
        body, sig = ticket.split(".")
        if not hmac.compare_digest(sig, _sign(body)):  # 시간차 공격을 막는 비교
            raise ValueError
        data = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="가입 정보가 올바르지 않습니다. 카카오 로그인을 다시 해 주세요.") from None
    if data["exp"] < time.time():
        raise HTTPException(status_code=400, detail="입력 시간이 지났습니다. 카카오 로그인을 다시 해 주세요.")
    return data


# ---------- 1) 카카오 로그인 화면 주소 ----------
@router.get("/login-url")
def login_url(state: str):
    """state: 프론트가 만든 임의 값. 돌아왔을 때 같은지 비교해 다른 사이트가 끼워 넣은 요청(CSRF)을 막는다"""
    require_config()
    params = {
        "client_id": KAKAO_REST_API_KEY,
        "redirect_uri": KAKAO_REDIRECT_URI,
        "response_type": "code",
        "state": state,
    }
    if KAKAO_PROMPT:
        # prompt=login: 이미 카카오에 로그인돼 있어도 로그인 화면을 다시 보여 줌 (시연용, config.py 참고)
        params["prompt"] = KAKAO_PROMPT
    query = urlencode(params)
    return {"url": f"{AUTHORIZE_URL}?{query}"}


# ---------- 2) 인가 코드로 로그인 ----------
class KakaoLoginRequest(BaseModel):
    code: str


def fetch_kakao_user(code: str) -> tuple[int, str | None]:
    """인가 코드 → 액세스 토큰 → (카카오 회원번호, 닉네임)"""
    token_form = {
        "grant_type": "authorization_code",
        "client_id": KAKAO_REST_API_KEY,
        "redirect_uri": KAKAO_REDIRECT_URI,
        "code": code,
    }
    if KAKAO_CLIENT_SECRET:
        token_form["client_secret"] = KAKAO_CLIENT_SECRET
    try:
        res = httpx.post(TOKEN_URL, data=token_form, timeout=10)
        if res.status_code != 200:
            # 인가 코드는 한 번만 쓸 수 있고 금방 만료된다(KOE320 등). 원인은 서버 로그로 남김
            print("카카오 토큰 발급 실패:", res.status_code, res.text)
            raise HTTPException(status_code=400, detail="카카오 인증에 실패했습니다. 다시 로그인해 주세요.")
        access_token = res.json()["access_token"]

        me = httpx.get(USER_URL, headers={"Authorization": f"Bearer {access_token}"}, timeout=10)
        if me.status_code != 200:
            print("카카오 사용자 정보 조회 실패:", me.status_code, me.text)
            raise HTTPException(status_code=502, detail="카카오 사용자 정보를 가져오지 못했습니다.")
        info = me.json()
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="카카오 서버에 연결할 수 없습니다.") from None

    profile = (info.get("kakao_account") or {}).get("profile") or {}
    nickname = profile.get("nickname") or (info.get("properties") or {}).get("nickname")
    return int(info["id"]), nickname


@router.post("/login")
def kakao_login(body: KakaoLoginRequest, db: Connection = Depends(get_db)):
    require_config()
    kakao_id, nickname = fetch_kakao_user(body.code)

    user = (
        db.execute(text("SELECT id, login_id, name FROM users WHERE kakao_id = :kid"), {"kid": kakao_id})
        .mappings()
        .first()
    )
    if user:
        return {"status": "ok", "user": dict(user)}
    # 처음 온 카카오 회원: 추가 정보를 받은 뒤에 만든다
    return {"status": "need_profile", "ticket": make_ticket(kakao_id, nickname), "nickname": nickname}


# ---------- 3) 첫 로그인 추가 정보 → 회원 생성 ----------
class KakaoSignupRequest(BaseModel):
    ticket: str
    name: str
    rrn_front: str  # 주민등록번호 앞 6자리 (생년월일·성별만 뽑고 저장하지 않음)
    rrn_back: str  # 뒷자리 첫 숫자
    phone: str

    @field_validator("name")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("이름을 입력해 주세요.")
        return value

    @field_validator("phone")
    @classmethod
    def check_phone(cls, value: str) -> str:
        if not PHONE_RULE.fullmatch(value):
            raise ValueError("휴대폰 번호 형식이 올바르지 않습니다.")
        return value


@router.post("/signup", response_model=LoginResponse, status_code=201)
def kakao_signup(body: KakaoSignupRequest, db: Connection = Depends(get_db)):
    data = read_ticket(body.ticket)
    kakao_id = int(data["kid"])
    try:
        birth_date, gender = parse_rrn(body.rrn_front, body.rrn_back)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    login_id = f"kakao_{kakao_id}"
    try:
        new_id = insert_returning_id(
            db,
            """
            INSERT INTO users (login_id, kakao_id, password, name, birth_date, gender, phone)
            VALUES (:login_id, :kakao_id, NULL, :name, :birth_date, :gender, :phone)
            """,
            {
                "login_id": login_id,
                "kakao_id": kakao_id,
                "name": body.name,
                "birth_date": birth_date,
                "gender": gender,
                "phone": body.phone,
            },
        )
        db.commit()
    except IntegrityError:
        # 같은 카카오 계정으로 두 번 제출(새로고침 등) → 이미 만들어진 회원으로 로그인시킴
        db.rollback()
        user = (
            db.execute(text("SELECT id, login_id, name FROM users WHERE kakao_id = :kid"), {"kid": kakao_id})
            .mappings()
            .first()
        )
        if user is None:
            raise HTTPException(status_code=409, detail="회원을 만들 수 없습니다. 잠시 후 다시 시도해 주세요.") from None
        return user

    # 김재필(1981-09-26, 남)으로 가입하면 미리 만들어 둔 시연 데이터를 바로 연결 (가입이 commit 된 뒤 따로 처리)
    seed_if_demo(db, login_id, body.name, birth_date, gender)
    return {"id": new_id, "login_id": login_id, "name": body.name}
