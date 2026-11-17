from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import Connection, text

from app.database import get_db

router = APIRouter(prefix="/api/report", tags=["report"])

# 리포트 각 칸에 보여 줄 질병 수
TOP_N = 3

COLUMNS = """
    disease, dna_risk, dna_stage, dna_summary,
    checkup_risk, checkup_stage, checkup_summary,
    checkup_values, checkup_opinion, insurance_view
"""

# 칸마다 "위험도 높은 순 → 같으면 가중치 높은 순 → 같으면 CSV 순서" 로 TOP_N개를 고른다.
# ORDER BY 에 들어갈 문장은 서버가 정한 고정 문자열만 쓴다 (사용자 입력을 넣지 않음)
ORDER = {
    # 위쪽 종합 분석: DNA 단계 + 건강검진 단계 합이 큰 순
    "matrix": "(dna_stage + checkup_stage) DESC, sort_order",
    # 건강검진으로 식별된 정보
    "checkup": "checkup_stage DESC, checkup_weight DESC, sort_order",
    # DNA로 식별된 정보
    "dna": "dna_stage DESC, dna_weight DESC, sort_order",
}


@router.get("/{user_id}")
def get_report(user_id: int, db: Connection = Depends(get_db)):
    # TODO: JWT가 생기면 경로의 user_id 대신 토큰의 회원 id를 쓰기 (지금은 다른 회원 id도 조회 가능)
    result = {}
    for key, order_by in ORDER.items():
        rows = (
            db.execute(
                text(
                    f"""
                    SELECT {COLUMNS}
                    FROM report_diseases
                    WHERE user_id = :user_id
                    ORDER BY {order_by}
                    LIMIT :limit
                    """
                ),
                {"user_id": user_id, "limit": TOP_N},
            )
            .mappings()
            .all()
        )
        result[key] = [dict(r) for r in rows]

    if not result["matrix"]:
        raise HTTPException(status_code=404, detail="리포트 데이터가 없습니다.")
    return result
