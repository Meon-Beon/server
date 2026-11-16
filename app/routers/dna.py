import base64
import secrets
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import Connection, text

from app.core.config import TOSS_SECRET_KEY
from app.database import get_db

router = APIRouter(prefix="/api/dna", tags=["dna"])

# 상품 정보는 서버가 정한다. 프론트에서 보낸 금액은 조작될 수 있기 때문
PRODUCT_NAME = "DTC 유전자 96종 종합 분석 키트 + 리포트"
PRODUCT_PRICE = 50_000

TOSS_CONFIRM_URL = "https://api.tosspayments.com/v1/payments/confirm"


def toss_auth_header() -> str:
    # 토스 인증 방식: "시크릿키:" (뒤에 콜론 필수)를 base64로 인코딩해 Basic 헤더로 보낸다
    token = base64.b64encode(f"{TOSS_SECRET_KEY}:".encode()).decode()
    return f"Basic {token}"


# ---------- 1) 주문 생성: 결제창을 띄우기 전에 호출 ----------
class OrderCreateRequest(BaseModel):
    user_id: int | None = None  # 로그인 안 했으면 비회원 주문


class OrderResponse(BaseModel):
    order_id: str
    order_name: str
    amount: int


@router.post("/orders", response_model=OrderResponse, status_code=201)
def create_order(body: OrderCreateRequest, db: Connection = Depends(get_db)):
    # 토스 규칙: 영문·숫자·-·_·= 로 된 6~64자. token_urlsafe는 영문·숫자·-·_ 만 만든다
    order_id = f"dna_{secrets.token_urlsafe(16)}"
    db.execute(
        text(
            """
            INSERT INTO dna_orders (order_id, user_id, order_name, amount)
            VALUES (:order_id, :user_id, :order_name, :amount)
            """
        ),
        {
            "order_id": order_id,
            "user_id": body.user_id,
            "order_name": PRODUCT_NAME,
            "amount": PRODUCT_PRICE,
        },
    )
    db.commit()
    return {"order_id": order_id, "order_name": PRODUCT_NAME, "amount": PRODUCT_PRICE}


# ---------- 2) 결제 승인: 토스 결제 후 successUrl로 돌아왔을 때 호출 ----------
class ConfirmRequest(BaseModel):
    payment_key: str
    order_id: str
    amount: int


class ConfirmResponse(BaseModel):
    order_id: str
    order_name: str
    amount: int
    method: str | None = None
    approved_at: datetime | None = None
    receipt_url: str | None = None


ORDER_COLUMNS = """
    order_id, order_name, amount, status, payment_key,
    method, approved_at, receipt_url
"""


@router.post("/payments/confirm", response_model=ConfirmResponse)
def confirm_payment(body: ConfirmRequest, db: Connection = Depends(get_db)):
    order = (
        db.execute(
            text(f"SELECT {ORDER_COLUMNS} FROM dna_orders WHERE order_id = :order_id"),
            {"order_id": body.order_id},
        )
        .mappings()
        .first()
    )
    if order is None:
        raise HTTPException(status_code=404, detail="주문을 찾을 수 없습니다.")

    # 이미 승인된 주문 (새로고침 등으로 다시 요청한 경우) -> 저장된 결과를 그대로 돌려준다
    if order["status"] == "DONE":
        if order["payment_key"] != body.payment_key:
            raise HTTPException(status_code=409, detail="이미 결제가 완료된 주문입니다.")
        return order

    # 핵심 검증: successUrl에 붙어 온 금액이 서버에 저장한 주문 금액과 같은지 확인
    # 다르면 누군가 금액을 조작한 것이므로 승인하지 않는다 (승인 안 된 결제는 자동 만료됨)
    if body.amount != order["amount"]:
        raise HTTPException(status_code=400, detail="결제 금액이 주문 금액과 다릅니다.")

    try:
        res = httpx.post(
            TOSS_CONFIRM_URL,
            json={
                "paymentKey": body.payment_key,
                "orderId": body.order_id,
                "amount": order["amount"],
            },
            headers={
                "Authorization": toss_auth_header(),
                # 같은 주문으로 여러 번 요청해도 한 번만 승인되게 하는 키
                "Idempotency-Key": body.order_id,
            },
            timeout=30,
        )
    except httpx.HTTPError:
        raise HTTPException(status_code=502, detail="결제사 서버와 통신하지 못했습니다.") from None

    data = res.json()

    if res.status_code != 200:
        # 토스 오류 응답 형식: {"code": "...", "message": "..."}
        message = data.get("message", "결제 승인에 실패했습니다.")
        db.execute(
            text(
                """
                UPDATE dna_orders SET status = 'FAILED', fail_reason = :reason
                WHERE order_id = :order_id
                """
            ),
            {"reason": f"{data.get('code')}: {message}"[:255], "order_id": body.order_id},
        )
        db.commit()
        raise HTTPException(status_code=400, detail=message)

    # approvedAt 예: "2026-11-16T14:30:00+09:00" -> DB DATETIME에는 시간대 없이 한국 시각으로 저장
    approved_at = None
    if data.get("approvedAt"):
        approved_at = datetime.fromisoformat(data["approvedAt"]).replace(tzinfo=None)
    receipt_url = (data.get("receipt") or {}).get("url")

    db.execute(
        text(
            """
            UPDATE dna_orders
            SET status = 'DONE', payment_key = :payment_key, method = :method,
                approved_at = :approved_at, receipt_url = :receipt_url, fail_reason = NULL
            WHERE order_id = :order_id
            """
        ),
        {
            "payment_key": body.payment_key,
            "method": data.get("method"),
            "approved_at": approved_at,
            "receipt_url": receipt_url,
            "order_id": body.order_id,
        },
    )
    db.commit()

    return {
        "order_id": body.order_id,
        "order_name": order["order_name"],
        "amount": order["amount"],
        "method": data.get("method"),
        "approved_at": approved_at,
        "receipt_url": receipt_url,
    }
