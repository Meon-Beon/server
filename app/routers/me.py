"""마이페이지: 회원 정보 조회·수정, 비밀번호 변경, 결제 내역"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import Connection, text

from app.database import get_db
from app.routers.auth import PASSWORD_RULE, PHONE_RULE, ZIPCODE_RULE, hash_password, verify_password

router = APIRouter(prefix="/api/me", tags=["me"])

# TODO: JWT가 생기면 경로의 user_id 대신 토큰의 회원 id를 쓰기 (지금은 다른 회원 id 로도 조회·수정 가능)


def load_me(db: Connection, user_id: int) -> dict:
    row = (
        db.execute(
            text(
                """
                SELECT id, login_id, name, birth_date, gender, phone, zipcode, address, address_detail,
                       created_at, kakao_id, password
                FROM users WHERE id = :id
                """
            ),
            {"id": user_id},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="회원 정보를 찾을 수 없습니다.")
    me = dict(row)
    # 비밀번호 해시와 카카오 회원번호는 화면에 필요 없으므로 내보내지 않고, 여부만 알려 준다
    me["login_type"] = "kakao" if me.pop("kakao_id") is not None else "local"
    me["has_password"] = me.pop("password") is not None
    return me


@router.get("/{user_id}")
def get_me(user_id: int, db: Connection = Depends(get_db)):
    return load_me(db, user_id)


# ---------- 정보 수정 (연락처·주소) ----------
class ProfileUpdate(BaseModel):
    phone: str
    zipcode: str = ""
    address: str = ""
    address_detail: str = ""

    @field_validator("phone")
    @classmethod
    def check_phone(cls, value: str) -> str:
        value = value.replace("-", "").strip()
        if not PHONE_RULE.fullmatch(value):
            raise ValueError("휴대폰 번호 형식이 올바르지 않습니다.")
        return value

    @field_validator("zipcode")
    @classmethod
    def check_zipcode(cls, value: str) -> str:
        value = value.strip()
        if value and not ZIPCODE_RULE.fullmatch(value):
            raise ValueError("우편번호 형식이 올바르지 않습니다.")
        return value


@router.patch("/{user_id}")
def update_me(user_id: int, body: ProfileUpdate, db: Connection = Depends(get_db)):
    load_me(db, user_id)  # 없는 회원이면 404
    if bool(body.zipcode) != bool(body.address.strip()):
        raise HTTPException(status_code=400, detail="주소 검색으로 우편번호와 주소를 함께 입력해 주세요.")
    db.execute(
        text(
            """
            UPDATE users
            SET phone = :phone, zipcode = :zipcode, address = :address, address_detail = :address_detail
            WHERE id = :id
            """
        ),
        {
            "id": user_id,
            "phone": body.phone,
            "zipcode": body.zipcode or None,
            "address": body.address.strip() or None,
            "address_detail": body.address_detail.strip() or None,
        },
    )
    db.commit()
    return load_me(db, user_id)


# ---------- 비밀번호 변경 ----------
class PasswordChange(BaseModel):
    current_password: str
    new_password: str

    @field_validator("new_password")
    @classmethod
    def check_password(cls, value: str) -> str:
        if not PASSWORD_RULE.fullmatch(value):
            raise ValueError("비밀번호는 영문, 숫자, 특수문자를 조합해 8자리 이상이어야 합니다.")
        return value


@router.post("/{user_id}/password", status_code=204)
def change_password(user_id: int, body: PasswordChange, db: Connection = Depends(get_db)):
    stored = db.execute(text("SELECT password FROM users WHERE id = :id"), {"id": user_id}).first()
    if stored is None:
        raise HTTPException(status_code=404, detail="회원 정보를 찾을 수 없습니다.")
    if stored[0] is None:
        raise HTTPException(status_code=400, detail="카카오로 가입한 계정은 비밀번호가 없어요.")
    if not verify_password(body.current_password, stored[0]):
        raise HTTPException(status_code=400, detail="현재 비밀번호가 올바르지 않습니다.")
    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="새 비밀번호가 현재 비밀번호와 같아요.")
    db.execute(
        text("UPDATE users SET password = :password WHERE id = :id"),
        {"id": user_id, "password": hash_password(body.new_password)},
    )
    db.commit()


# ---------- 결제 내역 (결제가 끝난 DNA 검사 주문만) ----------
@router.get("/{user_id}/orders")
def get_orders(user_id: int, db: Connection = Depends(get_db)):
    rows = db.execute(
        text(
            """
            SELECT order_id, order_name, amount, method, approved_at, receipt_url
            FROM dna_orders
            WHERE user_id = :user_id AND status = 'DONE'
            ORDER BY approved_at DESC
            """
        ),
        {"user_id": user_id},
    ).mappings()
    return {"orders": [dict(r) for r in rows]}
