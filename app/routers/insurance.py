from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import Connection, bindparam, text

from app.database import get_db, insert_returning_id

router = APIRouter(prefix="/api/insurance", tags=["insurance"])

# 보장명 → 보장 카테고리. 위에서부터 먼저 맞는 규칙을 쓴다 (예: "교통상해입원일당"은 상해보다 교통 상해가 먼저)
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("교통 상해", ("교통", "운전자", "자동차", "변호사", "벌금")),
    ("상해", ("상해", "골절", "후유장해")),
    ("암", ("암",)),
    ("뇌혈관", ("뇌혈관", "뇌졸중", "뇌출혈", "뇌경색")),
    ("심장", ("심장", "심근", "협심증")),
    ("당뇨", ("당뇨",)),
    ("치매·간병", ("치매", "장기요양", "간병")),
    ("실손 의료", ("실손", "의료비")),
    ("입원·수술", ("입원", "수술")),
]


def categorize(coverage_name: str) -> str:
    for label, keywords in CATEGORY_RULES:
        if any(k in coverage_name for k in keywords):
            return label
    return "기타"


def count_categories(coverages: list[dict]) -> list[str]:
    """보장 목록의 카테고리를 많이 나온 순서로 (같으면 먼저 나온 순서)"""
    counts: dict[str, int] = {}
    for c in coverages:
        label = categorize(c["coverage_name"])
        counts[label] = counts.get(label, 0) + 1
    return sorted(counts, key=lambda k: -counts[k])  # sorted 는 안정 정렬이라 동점이면 먼저 나온 것이 앞


@router.get("/existing/{user_id}")
def get_existing_insurance(user_id: int, db: Connection = Depends(get_db)):
    """회원이 이미 가입한 보험 목록. 상품마다 보장(coverages)·특성(features) 배열을 붙여 돌려준다.

    데이터는 import_existing_insurance.py 로 넣는다 (sql/existing_insurance.sql 테이블)
    """
    # TODO: JWT가 생기면 경로의 user_id 대신 토큰의 회원 id를 쓰기 (지금은 다른 회원 id도 조회 가능)

    # 1) 상품 목록
    products = [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT id, company_name, product_name, premium
                FROM user_insurance
                WHERE user_id = :user_id
                ORDER BY sort_order
                """
            ),
            {"user_id": user_id},
        ).mappings()
    ]

    # 2) 그 회원 상품들의 보장·특성을 한 번에 가져와 상품 id 별로 나눠 붙인다.
    #    상품마다 쿼리를 따로 날리면 상품 수만큼 DB를 왕복하게 된다 (N+1 문제)
    by_id = {p["id"]: {**p, "coverages": [], "features": []} for p in products}

    coverages = db.execute(
        text(
            """
            SELECT c.insurance_id, c.coverage_name, c.coverage_detail, c.coverage_amount
            FROM user_insurance_coverage c
            JOIN user_insurance i ON i.id = c.insurance_id
            WHERE i.user_id = :user_id
            ORDER BY c.insurance_id, c.sort_order
            """
        ),
        {"user_id": user_id},
    ).mappings()
    for c in coverages:
        item = dict(c)
        by_id[item.pop("insurance_id")]["coverages"].append(item)

    features = db.execute(
        text(
            """
            SELECT f.insurance_id, f.feature_name, f.feature_detail
            FROM user_insurance_feature f
            JOIN user_insurance i ON i.id = f.insurance_id
            WHERE i.user_id = :user_id
            ORDER BY f.insurance_id, f.sort_order
            """
        ),
        {"user_id": user_id},
    ).mappings()
    for t in features:
        item = dict(t)
        by_id[item.pop("insurance_id")]["features"].append(item)

    items = list(by_id.values())  # dict 는 넣은 순서를 지키므로 상품 순서(sort_order) 그대로
    for p in items:
        p["categories"] = count_categories(p["coverages"])

    # 가입한 모든 보장을 합쳐서 가장 많은 카테고리 = 주요 보장 카테고리
    all_coverages = [c for p in items for c in p["coverages"]]
    main = count_categories(all_coverages)

    # 가입한 보험이 없는 것도 정상 상태라서 404 대신 빈 목록을 돌려준다
    return {
        "total_premium": sum(p["premium"] for p in items),
        "main_category": main[0] if main else None,
        "products": items,
    }


@router.get("/design/{user_id}")
def get_insurance_design(user_id: int, db: Connection = Depends(get_db)):
    """보험설계 화면용 추천 보험(보장 포함)과 질병별 보장 공백 점검 결과.

    데이터는 import_insurance_design.py 로 넣는다 (sql/insurance_design.sql 테이블)
    """
    # TODO: JWT가 생기면 경로의 user_id 대신 토큰의 회원 id를 쓰기
    recs = [
        dict(r)
        for r in db.execute(
            text(
                """
                SELECT id, rec_rank, company_name, product_name, insurer_type, product_type,
                       premium, premium_basis, join_age, target_diseases, reason, checks, fit_status, join_url
                FROM user_recommendation
                WHERE user_id = :user_id
                ORDER BY rec_rank
                """
            ),
            {"user_id": user_id},
        ).mappings()
    ]
    by_id = {r["id"]: r for r in recs}
    for r in recs:
        # "심장 / 뇌혈관" → ["심장", "뇌혈관"] (프론트가 문자열을 다시 쪼개지 않게)
        r["target_diseases"] = [d.strip() for d in (r["target_diseases"] or "").split("/") if d.strip()]
        r["coverages"] = []

    coverages = db.execute(
        text(
            """
            SELECT c.recommendation_id, c.coverage_name, c.coverage_detail, c.coverage_amount
            FROM user_recommendation_coverage c
            JOIN user_recommendation r ON r.id = c.recommendation_id
            WHERE r.user_id = :user_id
            ORDER BY c.recommendation_id, c.sort_order
            """
        ),
        {"user_id": user_id},
    ).mappings()
    for c in coverages:
        item = dict(c)
        by_id[item.pop("recommendation_id")]["coverages"].append(item)

    gaps = [
        {**dict(g), "at_risk": bool(g["at_risk"])}  # MySQL BOOLEAN 은 0/1 로 오므로 true/false 로
        for g in db.execute(
            text(
                """
                SELECT disease, at_risk, diagnosed, status, current_summary, gap_detail
                FROM user_coverage_gap
                WHERE user_id = :user_id
                ORDER BY sort_order
                """
            ),
            {"user_id": user_id},
        ).mappings()
    ]

    return {"recommendations": recs, "gaps": gaps}


# ---------- 저장한 보험 설계 리포트 ("리포트로 저장" 1번 = 리포트 1개, 회원당 여러 개) ----------
MAX_REPORTS = 30  # 회원 한 명이 쌓아 둘 수 있는 리포트 수 (무한히 쌓이지 않게)


def load_reports(db: Connection, user_id: int, report_id: int | None = None) -> list[dict]:
    """리포트 목록(최근에 저장한 것부터). 각 리포트에 담은 상품(담은 순서)과 보장, 월 보험료 합계.

    상품을 다시 적재해서 담은 상품이 다 지워진 리포트는 INNER JOIN 이라 자연스럽게 빠진다
    """
    rows = db.execute(
        text(
            f"""
            SELECT p.id AS report_id, p.saved_at,
                   r.id, r.rec_rank, r.company_name, r.product_name, r.premium,
                   r.target_diseases, r.join_url
            FROM user_saved_report p
            JOIN user_saved_report_item i ON i.report_id = p.id
            JOIN user_recommendation r ON r.id = i.recommendation_id
            WHERE p.user_id = :user_id {"AND p.id = :report_id" if report_id else ""}
            ORDER BY p.saved_at DESC, p.id DESC, i.sort_order
            """
        ),
        {"user_id": user_id, "report_id": report_id},
    ).mappings()

    # 한 번의 JOIN 결과(리포트 × 상품)를 리포트별로 묶는다. dict 는 넣은 순서를 지키므로 정렬도 유지됨
    reports: dict[int, dict] = {}
    for row in rows:
        r = dict(row)
        rid = r.pop("report_id")
        report = reports.setdefault(rid, {"id": rid, "saved_at": r.pop("saved_at"), "total_premium": 0, "products": []})
        r.pop("saved_at", None)
        r["target_diseases"] = [d.strip() for d in (r["target_diseases"] or "").split("/") if d.strip()]
        report["total_premium"] += r["premium"] or 0
        report["products"].append(r)

    # 보장은 상품 id 기준으로 한 번만 조회 (같은 상품이 여러 리포트에 담겨 있어도 쿼리는 1번)
    product_ids = {p["id"] for rep in reports.values() for p in rep["products"]}
    coverages: dict[int, list[dict]] = {pid: [] for pid in product_ids}
    if product_ids:
        for c in db.execute(
            text(
                """
                SELECT recommendation_id, coverage_name, coverage_amount
                FROM user_recommendation_coverage
                WHERE recommendation_id IN :ids
                ORDER BY recommendation_id, sort_order
                """
            ).bindparams(bindparam("ids", expanding=True)),  # IN (…) 에 리스트를 넣을 때
            {"ids": list(product_ids)},
        ).mappings():
            item = dict(c)
            coverages[item.pop("recommendation_id")].append(item)
    for rep in reports.values():
        for p in rep["products"]:
            p["coverages"] = coverages[p["id"]]
        rep["owned"] = []
        rep["owned_total"] = 0

    # 리포트에 함께 넣은 기존 가입 보험 (보험설계 화면에서 (−)로 뺀 것은 저장되지 않음)
    if reports:
        owned_rows = [
            dict(r)
            for r in db.execute(
                text(
                    """
                    SELECT o.report_id, i.id, i.company_name, i.product_name, i.premium
                    FROM user_saved_report_owned o
                    JOIN user_insurance i ON i.id = o.insurance_id
                    WHERE o.report_id IN :ids
                    ORDER BY o.report_id, o.sort_order
                    """
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": list(reports)},
            ).mappings()
        ]
        # 카테고리(암, 상해 …)는 보유 보험 화면과 같은 규칙으로 보장명에서 뽑음
        owned_cov: dict[int, list[dict]] = {}
        owned_ids = list({o["id"] for o in owned_rows})
        if owned_ids:
            for c in db.execute(
                text(
                    "SELECT insurance_id, coverage_name FROM user_insurance_coverage WHERE insurance_id IN :ids"
                ).bindparams(bindparam("ids", expanding=True)),
                {"ids": owned_ids},
            ).mappings():
                owned_cov.setdefault(c["insurance_id"], []).append(dict(c))
        for o in owned_rows:
            rep = reports[o.pop("report_id")]
            o["categories"] = count_categories(owned_cov.get(o["id"], []))
            rep["owned"].append(o)
            rep["owned_total"] += o["premium"]

    return list(reports.values())


@router.get("/reports/{user_id}")
def get_reports(user_id: int, db: Connection = Depends(get_db)):
    """마이페이지 > 보험 설계 보러가기: 저장한 리포트 목록. 없으면 빈 목록"""
    # TODO: JWT가 생기면 경로의 user_id 대신 토큰의 회원 id를 쓰기
    return {"reports": load_reports(db, user_id)}


class SaveReportRequest(BaseModel):
    # 설계 칸에 담은 순서대로. 빈 리포트는 만들지 않음
    recommendation_ids: list[int] = Field(min_length=1, max_length=20)
    # 합계에 넣은 기존 가입 보험 (화면에서 (−)로 뺀 것은 빼고 보냄)
    owned_insurance_ids: list[int] = Field(default_factory=list, max_length=20)


@router.post("/reports/{user_id}", status_code=201)
def create_report(user_id: int, body: SaveReportRequest, db: Connection = Depends(get_db)):
    """보험설계 화면 "리포트로 저장": 지금 설계 칸의 조합을 새 리포트 하나로 추가 (예전 리포트는 그대로 둠)"""
    ids = list(dict.fromkeys(body.recommendation_ids))  # 순서는 지키고 중복만 제거

    # 다른 회원의 추천 상품 id 를 섞어 보내는 것을 막는다
    mine = set(
        db.execute(
            text("SELECT id FROM user_recommendation WHERE user_id = :user_id AND id IN :ids").bindparams(
                bindparam("ids", expanding=True)
            ),
            {"user_id": user_id, "ids": ids},
        ).scalars()
    )
    if mine != set(ids):
        raise HTTPException(status_code=400, detail="저장할 수 없는 상품이 포함돼 있어요. 화면을 새로고침해 주세요.")

    # 기존 가입 보험도 이 회원 것인지 확인
    owned_ids = list(dict.fromkeys(body.owned_insurance_ids))
    if owned_ids:
        mine_owned = set(
            db.execute(
                text("SELECT id FROM user_insurance WHERE user_id = :user_id AND id IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                ),
                {"user_id": user_id, "ids": owned_ids},
            ).scalars()
        )
        if mine_owned != set(owned_ids):
            raise HTTPException(status_code=400, detail="저장할 수 없는 기존 보험이 포함돼 있어요. 화면을 새로고침해 주세요.")

    count = db.execute(
        text("SELECT COUNT(*) FROM user_saved_report WHERE user_id = :user_id"), {"user_id": user_id}
    ).scalar_one()
    if count >= MAX_REPORTS:
        raise HTTPException(
            status_code=400, detail=f"리포트는 {MAX_REPORTS}개까지 저장할 수 있어요. 마이페이지에서 안 쓰는 리포트를 지워 주세요."
        )

    # 리포트 한 행 + 담은 상품 여러 행을 한 트랜잭션으로 (중간에 실패하면 반쪽짜리 리포트가 남지 않음)
    report_id = insert_returning_id(db, "INSERT INTO user_saved_report (user_id) VALUES (:user_id)", {"user_id": user_id})
    db.execute(
        text(
            """
            INSERT INTO user_saved_report_item (report_id, recommendation_id, sort_order)
            VALUES (:report_id, :rid, :sort_order)
            """
        ),
        [{"report_id": report_id, "rid": rid, "sort_order": i} for i, rid in enumerate(ids, start=1)],
    )
    if owned_ids:
        db.execute(
            text(
                """
                INSERT INTO user_saved_report_owned (report_id, insurance_id, sort_order)
                VALUES (:report_id, :iid, :sort_order)
                """
            ),
            [{"report_id": report_id, "iid": iid, "sort_order": i} for i, iid in enumerate(owned_ids, start=1)],
        )
    db.commit()
    return load_reports(db, user_id, report_id)[0]


@router.delete("/reports/{user_id}/{report_id}", status_code=204)
def delete_report(user_id: int, report_id: int, db: Connection = Depends(get_db)):
    """마이페이지에서 리포트 삭제. 담은 상품 행은 CASCADE 로 함께 지워진다"""
    # user_id 도 조건에 넣어 남의 리포트를 지우지 못하게 함
    result = db.execute(
        text("DELETE FROM user_saved_report WHERE id = :report_id AND user_id = :user_id"),
        {"report_id": report_id, "user_id": user_id},
    )
    if result.rowcount == 0:
        raise HTTPException(status_code=404, detail="이미 지워졌거나 없는 리포트예요.")
    db.commit()
