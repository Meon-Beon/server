"""main_portfolio_design.py 결과 폴더의 추천 보험·보장 공백 CSV를 보험설계 테이블에 넣는다.

사용법 (server 폴더에서, venv 켠 상태):
    python import_insurance_design.py "<결과 폴더 경로>" <login_id>
예:
    python import_insurance_design.py "new_try/portfolio_output/hu4BE6F2" 1234

폴더 안에서 읽는 파일 3개
- 추천보험_longform.csv        : 추천 상품과 보장 목록 (보장 1개 = 1행) ← 상품·보장의 기준
- insurance_recommendations.csv : 상품별 추천순위·보험료·대상질병·추천이유 (longform 에 없는 값만 가져옴)
- insurance_coverage_gaps.csv   : 질병별 보장 공백 점검
그리고 두 단계 위 폴더의 보험상품_wideform.csv(통합 상품 DB)에서 추천 상품의 가입URL 을 찾아 붙인다

- DB 접속 정보는 서버와 같은 server/.env 를 쓴다 (app/database.py 의 engine)
- 처음 한 번은 MySQL Workbench에서 sql/insurance_design.sql 을 실행해 테이블을 만들어 둬야 한다
- 같은 회원으로 다시 실행하면 그 회원의 기존 추천·공백 데이터를 지우고 새로 넣는다
"""
from __future__ import annotations

import csv
import sys
from pathlib import Path

from sqlalchemy import Connection, text

LONGFORM = "추천보험_longform.csv"
RECOMMENDATIONS = "insurance_recommendations.csv"
GAPS = "insurance_coverage_gaps.csv"
# 통합 상품 DB. 기본 위치는 결과 폴더(new_try/portfolio_output/<참가자>)의 두 단계 위 = new_try/보험상품_wideform.csv
PRODUCT_DB = "보험상품_wideform.csv"

LONGFORM_REQUIRED = {"보험구분", "회사명", "상품명", "보장명", "보장내용", "보장금액", "보험종류"}
REC_REQUIRED = {"participant", "추천순위", "회사명", "상품명", "보험료", "보험료기준", "가입연령",
                "대상질병", "추천이유", "확인필요사항", "적합성상태"}
GAP_REQUIRED = {"participant", "질병", "잠재위험대상", "진단여부", "점검상태", "현재보장요약", "공백또는확인사항"}

INSERT_REC = text(
    """
    INSERT INTO user_recommendation
        (user_id, participant, rec_rank, company_name, product_name, insurer_type, product_type,
         premium, premium_basis, join_age, target_diseases, reason, checks, fit_status, join_url)
    VALUES
        (:user_id, :participant, :rec_rank, :company_name, :product_name, :insurer_type, :product_type,
         :premium, :premium_basis, :join_age, :target_diseases, :reason, :checks, :fit_status, :join_url)
    """
)
INSERT_COVERAGE = text(
    """
    INSERT INTO user_recommendation_coverage
        (recommendation_id, sort_order, coverage_name, coverage_detail, coverage_amount)
    VALUES
        (:recommendation_id, :sort_order, :coverage_name, :coverage_detail, :coverage_amount)
    """
)
INSERT_GAP = text(
    """
    INSERT INTO user_coverage_gap
        (user_id, participant, sort_order, disease, at_risk, diagnosed, status, current_summary, gap_detail)
    VALUES
        (:user_id, :participant, :sort_order, :disease, :at_risk, :diagnosed, :status, :current_summary, :gap_detail)
    """
)


def read_csv(path: Path, required: set[str]) -> list[dict]:
    if not path.exists():
        raise ValueError(f"파일이 없습니다: {path}")
    # utf-8-sig: 엑셀용으로 파일 맨 앞에 붙은 BOM 문자를 떼고 읽는다
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path.name}에 필요한 열이 없습니다: {', '.join(sorted(missing))}")
        rows = [{k: (v or "").strip() for k, v in r.items()} for r in reader]
    if not rows:
        raise ValueError(f"{path.name}에 데이터가 없습니다.")
    return rows


def blank(value: str) -> str | None:
    return value or None


def to_int(value: str, label: str) -> int | None:
    value = value.replace(",", "")
    if not value:
        return None
    try:
        return int(float(value))
    except ValueError as e:
        raise ValueError(f"{label} {value!r} 가 숫자가 아닙니다.") from e


def read_join_urls(path: Path) -> dict[tuple[str, str], str]:
    """통합 상품 DB 에서 (회사명, 상품명) → 가입URL. 파일이나 열이 없으면 빈 dict (URL 없이 진행)"""
    if not path.exists():
        print(f"참고: {path} 가 없어 가입 URL 없이 넣습니다.")
        return {}
    with path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        if "가입URL" not in (reader.fieldnames or []):
            print(f"참고: {path.name} 에 가입URL 열이 없어 가입 URL 없이 넣습니다.")
            return {}
        urls = {}
        for r in reader:
            url = (r["가입URL"] or "").strip()
            # http(s) 주소만 저장 ("ㅡ" 같은 빈칸 표시나 javascript: 같은 위험한 주소는 버림)
            if url.lower().startswith(("http://", "https://")):
                urls[((r["회사명"] or "").strip(), (r["상품명"] or "").strip())] = url
    return urls


def read_products(folder: Path, product_db: Path | None = None) -> tuple[str, list[dict]]:
    """longform 을 상품별로 묶고, 같은 (회사명, 상품명)의 추천 정보를 붙인다. (참가자 코드, 상품 목록) 반환"""
    longform = read_csv(folder / LONGFORM, LONGFORM_REQUIRED)
    recs = read_csv(folder / RECOMMENDATIONS, REC_REQUIRED)

    participants = {r["participant"] for r in recs}
    if len(participants) != 1:
        raise ValueError(f"{RECOMMENDATIONS} 하나에는 한 사람만 있어야 합니다: {sorted(participants)}")
    participant = participants.pop()

    rec_by_key = {(r["회사명"], r["상품명"]): r for r in recs}
    join_urls = read_join_urls(product_db or folder.parent.parent / PRODUCT_DB)

    # dict 는 넣은 순서를 지키므로 longform 에 처음 나온 순서대로 상품이 쌓인다
    products: dict[tuple[str, str], dict] = {}
    for row in longform:
        key = (row["회사명"], row["상품명"])
        if key not in products:
            products[key] = {
                "company_name": row["회사명"],
                "product_name": row["상품명"],
                "insurer_type": blank(row["보험구분"]),
                "product_type": blank(row["보험종류"]),
                "coverages": [],
            }
        p = products[key]
        p["coverages"].append(
            {
                "sort_order": len(p["coverages"]) + 1,
                "coverage_name": row["보장명"],
                "coverage_detail": blank(row["보장내용"]),
                "coverage_amount": blank(row["보장금액"]),
            }
        )

    missing = [f"{c} {n}" for (c, n) in products if (c, n) not in rec_by_key]
    if missing:
        # 추천순위·보험료가 없으면 화면에 순서도 가격도 못 보여 주므로 조용히 넘어가지 않는다
        raise ValueError(f"{RECOMMENDATIONS}에 없는 상품이 있습니다: {missing}")

    result = []
    for key, p in products.items():
        r = rec_by_key[key]
        name = f"{p['company_name']} {p['product_name']}"
        result.append(
            {
                **p,
                "participant": participant,
                "rec_rank": to_int(r["추천순위"], f"{name} 추천순위"),
                "premium": to_int(r["보험료"], f"{name} 보험료"),
                "premium_basis": blank(r["보험료기준"]),
                "join_age": blank(r["가입연령"]),
                "target_diseases": blank(r["대상질병"]),
                "reason": blank(r["추천이유"]),
                # 확인필요사항 끝에 붙은 " / " 같은 빈 구분자는 떼어 냄
                "checks": blank(r["확인필요사항"].strip(" /")),
                "fit_status": blank(r["적합성상태"]),
                "join_url": join_urls.get(key),
            }
        )
    result.sort(key=lambda p: p["rec_rank"])
    return participant, result


def read_gaps(folder: Path, participant: str) -> list[dict]:
    rows = read_csv(folder / GAPS, GAP_REQUIRED)
    others = {r["participant"] for r in rows} - {participant}
    if others:
        raise ValueError(f"{GAPS}의 참가자가 추천 파일과 다릅니다: {sorted(others)}")
    return [
        {
            "participant": participant,
            "sort_order": i,
            "disease": r["질병"],
            "at_risk": r["잠재위험대상"] == "예",
            "diagnosed": blank(r["진단여부"]),
            "status": r["점검상태"],
            "current_summary": blank(r["현재보장요약"]),
            "gap_detail": blank(r["공백또는확인사항"]),
        }
        for i, r in enumerate(rows, start=1)
    ]


def save(conn: Connection, login_id: str, products: list[dict], gaps: list[dict]) -> int:
    """login_id 회원의 추천·공백을 바꿔 넣고, 그 회원의 users.id 를 돌려준다"""
    user_id = conn.execute(
        text("SELECT id FROM users WHERE login_id = :login_id"), {"login_id": login_id}
    ).scalar()
    if user_id is None:
        raise ValueError(f"login_id {login_id!r} 회원이 없습니다. 회원가입부터 해 주세요.")

    # 보장은 ON DELETE CASCADE 라서 추천 상품만 지우면 같이 지워진다
    conn.execute(text("DELETE FROM user_recommendation WHERE user_id = :user_id"), {"user_id": user_id})
    conn.execute(text("DELETE FROM user_coverage_gap WHERE user_id = :user_id"), {"user_id": user_id})

    for p in products:
        row = {k: v for k, v in p.items() if k != "coverages"}
        rec_id = conn.execute(INSERT_REC, {**row, "user_id": user_id}).lastrowid  # 방금 넣은 행의 id
        conn.execute(INSERT_COVERAGE, [{**c, "recommendation_id": rec_id} for c in p["coverages"]])

    conn.execute(INSERT_GAP, [{**g, "user_id": user_id} for g in gaps])
    return user_id


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    folder, login_id = Path(sys.argv[1]), sys.argv[2]

    participant, products = read_products(folder)
    gaps = read_gaps(folder, participant)

    from app.database import engine  # .env 를 읽어 DB 에 접속하는 서버의 engine 을 그대로 쓴다

    # begin(): 블록이 끝나면 commit, 중간에 오류가 나면 rollback
    with engine.begin() as conn:
        user_id = save(conn, login_id, products, gaps)

    n_cov = sum(len(p["coverages"]) for p in products)
    print(
        f"저장 완료: {participant} → 회원 {login_id} (users.id={user_id}), "
        f"추천 상품 {len(products)}개 · 보장 {n_cov}개 · 공백 점검 질병 {len(gaps)}개"
    )


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"오류: {e}")
        sys.exit(1)
