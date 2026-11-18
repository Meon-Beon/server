"""main_portfolio_design.py가 만든 health_portfolio_long.csv를 리포트 테이블(report_diseases)에 넣는다.

사용법 (server 폴더에서, venv 켠 상태):
    python import_report.py "<CSV 경로>" <login_id>
예:
    python import_report.py "new_try/portfolio_output/hu4BE6F2/health_portfolio_long.csv" 1234

- 새 형식(13열) CSV는 같은 폴더의 health_portfolio.json 에서 DNA가중치·건강검진가중치·보험검토관점을 가져온다.
  예전 형식 CSV(그 열들이 CSV 안에 있음)도 그대로 넣을 수 있다

- DB 접속 정보는 서버와 같은 server/.env 를 쓴다 (app/database.py 의 engine)
- 처음 한 번은 MySQL Workbench에서 sql/report.sql 을 실행해 테이블을 만들어 둬야 한다
- 같은 회원으로 다시 실행하면 그 회원의 기존 리포트 데이터를 지우고 새로 넣는다
"""
from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

from sqlalchemy import Connection, text

# 위험분류 → 5단계 눈금. 평가불가(데이터 없음)도 1단계로 본다
STAGE = {"고위험군": 5, "위험군": 3, "일반군": 1, "평가불가": 1}

# main_portfolio_design.py 가 "정보 없음" 으로 채워 둔 칸은 NULL 로 저장 (화면 문구는 프론트가 정함)
EMPTY = {"", "검진수치 정보 없음", "판정소견 정보 없음", "관련 검진 정보 없음", "유전적 특징에 대한 근거 정보 없음"}

# CSV 열 이름 → DB 칼럼 이름 (글로 된 칸만. 단계·가중치는 아래에서 따로 변환)
TEXT_COLUMNS = {
    "DNA": "dna_summary",
    "검진": "checkup_summary",
    "검진수치": "checkup_values",
    "판정소견": "checkup_opinion",
    "보험검토관점": "insurance_view",
}
# CSV에 꼭 있어야 하는 열
REQUIRED = {"participant", "질병", "DNA위험분류", "건강검진위험분류", *(set(TEXT_COLUMNS) - {"보험검토관점"})}
# 새 main_portfolio_design.py 의 화면용 CSV(13열)에는 없고 같은 폴더의 health_portfolio.json 에만 있는 열.
# CSV에 있으면(예전 형식) CSV 값을, 없으면 JSON 값을 쓴다
EXTRA = ("DNA가중치", "건강검진가중치", "보험검토관점")
PORTFOLIO_JSON = "health_portfolio.json"

INSERT_SQL = text(
    """
    INSERT INTO report_diseases
        (user_id, participant, disease, sort_order,
         dna_risk, dna_stage, dna_weight, dna_summary,
         checkup_risk, checkup_stage, checkup_weight, checkup_summary,
         checkup_values, checkup_opinion, insurance_view)
    VALUES
        (:user_id, :participant, :disease, :sort_order,
         :dna_risk, :dna_stage, :dna_weight, :dna_summary,
         :checkup_risk, :checkup_stage, :checkup_weight, :checkup_summary,
         :checkup_values, :checkup_opinion, :insurance_view)
    """
)


def clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return None if value in EMPTY else value


def stage(risk: str, disease: str) -> int:
    if risk not in STAGE:
        # 모르는 분류가 오면 조용히 넘어가지 않고 멈춘다
        raise ValueError(f"{disease}: 알 수 없는 위험분류 {risk!r}")
    return STAGE[risk]


def load_extras(csv_path: Path, fieldnames: list[str]) -> dict[str, dict]:
    """CSV에 없는 EXTRA 열을 health_portfolio.json 에서 질병별로 읽어 온다. {질병: {열: 값}}"""
    missing = [c for c in EXTRA if c not in fieldnames]
    if not missing:
        return {}  # 예전 형식 CSV: 전부 CSV 안에 있음
    json_path = csv_path.with_name(PORTFOLIO_JSON)
    if not json_path.exists():
        raise ValueError(
            f"CSV에 {', '.join(missing)} 열이 없고, 같은 폴더에 {PORTFOLIO_JSON} 도 없습니다. "
            "main_portfolio_design.py 포트폴리오 모드가 만든 두 파일을 같은 폴더에 두세요."
        )
    data = json.loads(json_path.read_text(encoding="utf-8"))
    extras = {}
    for row in data.get("disease_rows", []):
        extras[str(row["질병"]).strip()] = {c: row.get(c) for c in missing}
    return extras


def read_rows(csv_path: Path) -> list[dict]:
    """CSV 한 줄(질병 하나)을 DB에 넣을 dict 하나로 바꾼다. user_id 는 아직 비워 둠"""
    # utf-8-sig: 엑셀용으로 파일 맨 앞에 붙은 BOM 문자를 떼고 읽는다
    with csv_path.open(encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        missing = REQUIRED - set(fieldnames)
        if missing:
            raise ValueError(f"CSV에 필요한 열이 없습니다: {', '.join(sorted(missing))}")
        raw = list(reader)

    if not raw:
        raise ValueError("CSV에 데이터가 없습니다.")
    participants = {r["participant"].strip() for r in raw}
    if len(participants) != 1:
        raise ValueError(f"CSV 하나에는 한 사람만 있어야 합니다: {sorted(participants)}")

    extras = load_extras(csv_path, fieldnames)

    rows = []
    for order, r in enumerate(raw, start=1):
        disease = r["질병"].strip()
        if extras:
            if disease not in extras:
                raise ValueError(f"{disease}: {PORTFOLIO_JSON} 에 이 질병이 없습니다.")
            r = {**r, **{k: "" if v is None else str(v) for k, v in extras[disease].items()}}
        dna_risk, checkup_risk = r["DNA위험분류"].strip(), r["건강검진위험분류"].strip()
        row = {
            "participant": r["participant"].strip(),
            "disease": disease,
            "sort_order": order,  # CSV에 적힌 순서. 같은 단계·가중치일 때 마지막 기준
            "dna_risk": dna_risk,
            "dna_stage": stage(dna_risk, disease),
            "dna_weight": weight(r["DNA가중치"], disease, "DNA가중치"),
            "checkup_risk": checkup_risk,
            "checkup_stage": stage(checkup_risk, disease),
            "checkup_weight": weight(r["건강검진가중치"], disease, "건강검진가중치"),
        }
        for csv_col, db_col in TEXT_COLUMNS.items():
            row[db_col] = clean(r[csv_col])
        rows.append(row)
    return rows


def weight(value: str, disease: str, label: str) -> float:
    try:
        return float(value)
    except ValueError as e:
        raise ValueError(f"{disease}: {label} {value!r} 가 숫자가 아닙니다.") from e


def save_report(conn: Connection, login_id: str, rows: list[dict]) -> int:
    """login_id 회원의 리포트를 rows 로 바꿔 넣고, 그 회원의 users.id 를 돌려준다"""
    user_id = conn.execute(
        text("SELECT id FROM users WHERE login_id = :login_id"), {"login_id": login_id}
    ).scalar()
    if user_id is None:
        raise ValueError(f"login_id {login_id!r} 회원이 없습니다. 회원가입부터 해 주세요.")

    conn.execute(text("DELETE FROM report_diseases WHERE user_id = :user_id"), {"user_id": user_id})
    # 리스트를 넘기면 같은 INSERT 를 행마다 실행한다 (executemany)
    conn.execute(INSERT_SQL, [{**row, "user_id": user_id} for row in rows])
    return user_id


def main() -> None:
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    csv_path, login_id = Path(sys.argv[1]), sys.argv[2]

    rows = read_rows(csv_path)

    from app.database import engine  # .env 를 읽어 DB 에 접속하는 서버의 engine 을 그대로 쓴다

    # begin(): 블록이 끝나면 commit, 중간에 오류가 나면 rollback (지우기만 되고 넣기는 실패하는 일이 없음)
    with engine.begin() as conn:
        user_id = save_report(conn, login_id, rows)

    print(f"저장 완료: {rows[0]['participant']} → 회원 {login_id} (users.id={user_id}), 질병 {len(rows)}개")


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:
        print(f"오류: {e}")
        sys.exit(1)
