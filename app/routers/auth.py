import hashlib
import hmac
import re
import secrets
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import Connection, text
from sqlalchemy.exc import IntegrityError

from app.database import get_db

router = APIRouter(prefix="/api", tags=["auth"])

# 프론트(Signup.tsx)와 같은 규칙. 프론트 검사는 우회할 수 있으니 서버에서도 다시 검사한다
LOGIN_ID_RULE = re.compile(r"^[A-Za-z0-9]{6,16}$")
PASSWORD_RULE = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z\d]).{8,}$")
PHONE_RULE = re.compile(r"^01[016789]\d{7,8}$")
ZIPCODE_RULE = re.compile(r"^\d{5}$")

PBKDF2_ITERATIONS = 600_000


# ---------- 비밀번호 해시 ----------
# 비밀번호는 원문 그대로 저장하지 않고, 되돌릴 수 없는 해시로 바꿔서 저장한다.
# 저장 형식: pbkdf2_sha256$반복횟수$솔트$해시
def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)  # 같은 비밀번호라도 사람마다 해시가 달라지게 하는 임의 값
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode(), bytes.fromhex(salt), PBKDF2_ITERATIONS
    )
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algorithm, iterations, salt, expected = stored.split("$")
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), bytes.fromhex(salt), int(iterations)
        )
    except ValueError:
        # 해시 형식이 아닌 값(예전 평문 비밀번호)은 로그인 실패로 처리
        return False
    return hmac.compare_digest(digest.hex(), expected)


def parse_rrn(front: str, back: str) -> tuple[date, str]:
    """주민등록번호 앞 6자리 + 뒷자리 첫 숫자에서 생년월일과 성별만 뽑는다.

    주민등록번호 자체는 저장하지 않는다.
    """
    if not re.fullmatch(r"\d{6}", front) or not re.fullmatch(r"[1-8]", back):
        raise ValueError("주민등록번호 형식이 올바르지 않습니다.")
    century = 1900 if back in "1256" else 2000
    try:
        birth_date = date(century + int(front[:2]), int(front[2:4]), int(front[4:6]))
    except ValueError:
        raise ValueError("주민등록번호 형식이 올바르지 않습니다.") from None
    gender = "M" if int(back) % 2 == 1 else "F"
    return birth_date, gender


# ---------- 로그인 ----------
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
    # :login_id 는 자리표시자. 값을 문자열로 이어 붙이지 않고
    # 따로 넘겨야 SQL 인젝션을 막을 수 있다
    query = text(
        """
        SELECT id, login_id, name, password
        FROM users
        WHERE login_id = :login_id
        """
    )
    row = db.execute(query, {"login_id": body.login_id}).mappings().first()

    # 아이디가 없거나 비밀번호가 틀리면 로그인 실패 -> 401 Unauthorized
    # (둘 중 무엇이 틀렸는지는 알려주지 않는다)
    if row is None or not verify_password(body.password, row["password"]):
        raise HTTPException(
            status_code=401, detail="아이디 또는 비밀번호가 올바르지 않습니다."
        )

    # TODO: 로그인 상태 유지를 위한 JWT 발급
    return row


# ---------- 회원가입 ----------
class SignupRequest(BaseModel):
    name: str
    rrn_front: str  # 주민등록번호 앞 6자리 (YYMMDD)
    rrn_back: str  # 주민등록번호 뒷자리 첫 숫자
    zipcode: str
    address: str
    address_detail: str = ""
    phone: str
    login_id: str
    password: str

    @field_validator("name", "address")
    @classmethod
    def not_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("필수 항목이 비어 있습니다.")
        return value

    @field_validator("zipcode")
    @classmethod
    def check_zipcode(cls, value: str) -> str:
        if not ZIPCODE_RULE.fullmatch(value):
            raise ValueError("우편번호 형식이 올바르지 않습니다.")
        return value

    @field_validator("phone")
    @classmethod
    def check_phone(cls, value: str) -> str:
        if not PHONE_RULE.fullmatch(value):
            raise ValueError("휴대폰 번호 형식이 올바르지 않습니다.")
        return value

    @field_validator("login_id")
    @classmethod
    def check_login_id(cls, value: str) -> str:
        if not LOGIN_ID_RULE.fullmatch(value):
            raise ValueError("아이디는 영문, 숫자 6~16자로 입력해 주세요.")
        return value

    @field_validator("password")
    @classmethod
    def check_password(cls, value: str) -> str:
        if not PASSWORD_RULE.fullmatch(value):
            raise ValueError("비밀번호는 영문, 숫자, 특수문자를 조합해 8자리 이상이어야 합니다.")
        return value


def login_id_exists(db: Connection, login_id: str) -> bool:
    row = db.execute(
        text("SELECT 1 FROM users WHERE login_id = :login_id"), {"login_id": login_id}
    ).first()
    return row is not None


@router.get("/check-id")
def check_id(login_id: str, db: Connection = Depends(get_db)):
    """아이디 중복확인. 사용할 수 있으면 available=true"""
    if not LOGIN_ID_RULE.fullmatch(login_id):
        raise HTTPException(status_code=400, detail="아이디는 영문, 숫자 6~16자로 입력해 주세요.")
    return {"available": not login_id_exists(db, login_id)}


@router.post("/signup", response_model=LoginResponse, status_code=201)
def signup(body: SignupRequest, db: Connection = Depends(get_db)):
    try:
        birth_date, gender = parse_rrn(body.rrn_front, body.rrn_back)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from None

    if login_id_exists(db, body.login_id):
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.")

    query = text(
        """
        INSERT INTO users
            (login_id, password, name, birth_date, gender,
             phone, zipcode, address, address_detail)
        VALUES
            (:login_id, :password, :name, :birth_date, :gender,
             :phone, :zipcode, :address, :address_detail)
        """
    )
    try:
        result = db.execute(
            query,
            {
                "login_id": body.login_id,
                "password": hash_password(body.password),
                "name": body.name,
                "birth_date": birth_date,
                "gender": gender,
                "phone": body.phone,
                "zipcode": body.zipcode,
                "address": body.address,
                "address_detail": body.address_detail.strip(),
            },
        )
        db.commit()  # INSERT는 commit을 해야 실제로 저장된다
    except IntegrityError:
        # 중복확인과 INSERT 사이에 다른 사람이 같은 아이디로 먼저 가입한 경우
        # (login_id 컬럼의 UNIQUE 제약이 막아 준다)
        db.rollback()
        raise HTTPException(status_code=409, detail="이미 사용 중인 아이디입니다.") from None

    return {"id": result.lastrowid, "login_id": body.login_id, "name": body.name}
