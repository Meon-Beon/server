"""임의로 만든 기존 가입 보험 CSV(existing_insurance.csv)를 기존 보험 테이블 3개에 넣는다.

사용법 (server 폴더에서, venv 켠 상태):
    python import_existing_insurance.py "<CSV 경로>" <login_id>
예:
    python import_existing_insurance.py "new_try/existing_insurance.csv" 1234

- DB 접속 정보는 서버와 같은 server/.env 를 쓴다 (app/database.py 의 engine)
- 처음 한 번은 MySQL Workbench에서 sql/existing_insurance.sql 을 실행해 테이블을 만들어 둬야 한다
- 같은 회원으로 다시 실행하면 그 회원의 기존 보험 데이터를 지우고 새로 넣는다
- CSV 한 줄 = 상품 하나. 보장정보·특성정보 칸은 JSON 배열 글자라서 풀어서 한 원소씩 행으로 넣는다
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from sqlalchemy import Connection, text

REQUIRED = {"participant", "회사명", "상품명", "보장정보", "보험료", "특성정보"}

INSERT_PRODUCT = text(
    """
    INSERT INTO user_insurance
        (user_id, participant, sort_order, company_name, product_name, premium)
    VALUES
        (:user_id, :participant, :sort_order, :company_name, :product_name, :premium)
    """
)
INSERT_COVERAGE = text(
    """
    INSERT INTO user_insurance_coverage
        (insurance_id, sort_order, coverage_name, coverage_detail, coverage_amount)
    VALUES
        (:insurance_id, :sort_order, :coverage_name, :coverage_detail, :coverage_amount)
    """
)
INSERT_FEATURE = text(
    """
    INSERT INTO user_insurance_feature
        (insurance_id, sort_order, feature_name, feature_detail)
    VALUES
        (:insurance_id, :sort_order, :feature_name, :feature_detail)
    """
)


def parse_list(value: str, label: str, product: str) -> list[dict]:
    """JSON 배열 글자를 파이썬 리스트로. 비어 있으면 빈 리스트"""
    value = (value or "").strip()
    if not value:
        return []
    try:
        items = json.loads(value)
    except json.JSONDecodeError as e:
        raise ValueError(f"{product}: {label} 칸이 올바른 JSON이 아닙니다 ({e})") from e
    if not isinstance(items, list):
        raise ValueError(f"{product}: {label} 칸은 JSON 배열이어야 합니다.")
    return items


def read_products(csv_path: Path) -> list[dict]:
    """CSV 한 줄(상품 하나)을 {상품 칼럼…, coverages: [...], features: [...]} dict 하나로 바꾼다"""
    # utf-8-sig: 엑셀용으로 파일 맨 앞에 붙은 BOM 문자를 떼고 읽는다
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = REQUIRED - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"CSV에 필요한 열이 없습니다: {', '.join(sorted(missing))}")
        raw = list(reader)

    if not raw:
        raise ValueError("CSV에 데이터가 없습니다.")
    participants = {r["participant"].strip() for r in raw}
    if len(participants) != 1:
        raise ValueError(f"CSV 하나에는 한 사람만 있어야 합니다: {sorted(participants)}")

    products = []
    for order, r in enumerate(raw, start=1):
        name = r["상품명"].strip()
        try:
            premium = int(r["보험료"].replace(",", "").strip())  # "2,910" 처럼 쉼표가 있어도 처리
        except ValueError as e:
            raise ValueError(f"{name}: 보험료 {r['보험료']!r} 가 숫자가 아닙니다.") from e

        coverages = [
            {
                "sort_order": i,
                "coverage_name": c["보장명"].strip(),
                "coverage_detail": (c.get("보장내용") or "").strip() or None,
                "coverage_amount": c["보장금액"].strip(),
            }
            for i, c in enumerate(parse_list(r["보장정보"], "보장정보", name), start=1)
        ]
        features = [
            {
                "sort_order": i,
                "feature_name": t["특성명"].strip(),
                "feature_detail": (t.get("특성내용") or "").strip() or None,
            }
            for i, t in enumerate(parse_list(r["특성정보"], "특성정보", name), start=1)
        ]
        products.append(
            {
                "participant": r["participant"].strip(),
                "sort_order": order,
                "company_name": r["회사명"].strip(),
                "product_name": name,
                "premium": premium,
                "coverages": coverages,
                "features": features,
            }
        )
    return products


def save_products(conn: Connection, login_id: str, products: list[dict]) -> int:
    """login_id 회원의 기존 보험을 products 로 바꿔 넣고, 그 회원의 users.id 를 돌려준다"""
    user_id = conn.execute(
        text("SELECT id FROM users WHERE login_id = :login_id"), {"login_id": login_id}
    ).scalar()
    if user_id is None:
        raise ValueError(f"login_id {login_id!r} 회원이 없습니다. 회원가입부터 해 주세요.")

    # 보장·특성은 ON DELETE CASCADE 라서 상품만 지우면 같이 지워진다
    conn.execute(text("DELETE FROM user_insurance WHERE user_id = :user_id"), {"user_id": user_id})

    for p in products:
        product_row = {k: v for k, v in p.items() if k not in ("coverages", "features")}
        result = conn.execute(INSERT_PRODUCT, {**product_row, "user_id": user_id})
        insurance_id = result.lastrowid  # 방금 넣은 상품 행의 id (AUTO_INCREMENT 값)

        # 리스트를 넘기면 같은 INSERT 를 행마다 실행한다 (executemany)
        if p["coverages"]:
            conn.execute(INSERT_COVERAGE, [{**c, "insurance_id": insurance_id} for c in p["coverages"]])
        if p["features"]:
            conn.execute(INSERT_FEATURE, [{**t, "insurance_id": insurance_id} for t in p["features"]])
    return user_id


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    csv_path, login_id = Path(sys.argv[1]), sys.argv[2]

    products = read_products(csv_path)

    from app.database import engine  # .env 를 읽어 DB 에 접속하는 서버의 engine 을 그대로 쓴다

    # begin(): 블록이 끝나면 commit, 중간에 오류가 나면 rollback (지우기만 되고 넣기는 실패하는 일이 없음)
    with engine.begin() as conn:
        user_id = save_products(conn, login_id, products)

    n_cov = sum(len(p["coverages"]) for p in products)
    n_feat = sum(len(p["features"]) for p in products)
    print(
        f"저장 완료: {products[0]['participant']} → 회원 {login_id} (users.id={user_id}), "
        f"상품 {len(products)}개 · 보장 {n_cov}개 · 특성 {n_feat}개"
    )


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"오류: {e}")
        sys.exit(1)
