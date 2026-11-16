import json
import re
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy import Connection, text

from app.database import get_db

router = APIRouter(prefix="/api/health", tags=["health"])

PHONE_RULE = re.compile(r"^01[016789]\d{7,8}$")


# ---------- 건강검진 내역 조회 ----------
# 팝업에서 본인 확인한 값. 주민등록번호는 프론트에서 생년월일·성별로 바꿔서 보내고, 서버도 저장하지 않는다
class CheckupQuery(BaseModel):
    name: str
    birth_date: date
    gender: str  # M / F
    phone: str  # 숫자만 또는 010-1234-5678

    @field_validator("gender")
    @classmethod
    def check_gender(cls, v: str) -> str:
        if v not in ("M", "F"):
            raise ValueError("성별 값이 올바르지 않습니다.")
        return v

    @field_validator("phone")
    @classmethod
    def check_phone(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v)
        if not PHONE_RULE.fullmatch(digits):
            raise ValueError("휴대폰 번호 형식이 올바르지 않습니다.")
        return digits


@router.post("/checkups")
def find_checkups(body: CheckupQuery, db: Connection = Depends(get_db)):
    rows = (
        db.execute(
            text(
                """
                SELECT checkup_date, hospital, result
                FROM health_checkups
                WHERE name = :name AND birth_date = :birth_date
                  AND gender = :gender AND phone = :phone
                ORDER BY checkup_date DESC
                """
            ),
            {
                "name": body.name.strip(),
                "birth_date": body.birth_date,
                "gender": body.gender,
                "phone": body.phone,
            },
        )
        .mappings()
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail="일치하는 건강검진 내역이 없습니다.")

    checkups = []
    for r in rows:
        # MySQL JSON 칼럼은 드라이버에 따라 문자열로 오기도 해서 한 번 더 풂
        result = r["result"] if isinstance(r["result"], dict) else json.loads(r["result"])
        checkups.append(
            {"date": r["checkup_date"].isoformat(), "hospital": r["hospital"], **result}
        )
    return {"checkups": checkups}
