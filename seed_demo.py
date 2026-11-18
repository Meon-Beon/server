"""시연 데이터를 명령 하나로 DB에 넣는다: 건강 리포트 + 보유 보험 + 추천 보험·보장 공백.

사용법 (server 폴더에서, venv 켠 상태):
    python seed_demo.py              # 기본: 회원 1234, 참가자 hu4BE6F2
    python seed_demo.py 1234         # 회원 아이디 지정
    python seed_demo.py 1234 --participant hu4BE6F2

전에 한 번만: MySQL Workbench에서 sql 폴더의 아래 파일을 실행해 테이블을 만들어 둬야 한다
    login.sql → signup.sql → report.sql, existing_insurance.sql, insurance_design.sql

- 읽는 파일 (기본 위치, 옵션으로 바꿀 수 있음)
    new_try/portfolio_output/<참가자>/health_portfolio_long.csv (+ health_portfolio.json)  → 리포트
    new_try/existing_insurance.csv                                                     → 보유 보험
    new_try/portfolio_output/<참가자>/추천보험_longform.csv 외 2개                        → 추천·공백
- 세 가지를 하나의 트랜잭션으로 넣는다. 하나라도 실패하면 아무것도 바뀌지 않는다 (반쯤 들어간 상태가 생기지 않음)
- 같은 회원으로 다시 실행하면 그 회원의 기존 데이터를 지우고 새로 넣는다
- 실제 저장 로직은 import_report.py / import_existing_insurance.py / import_insurance_design.py 의 함수를 그대로 쓴다
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy.exc import OperationalError, ProgrammingError

import import_existing_insurance as existing
import import_insurance_design as design
import import_report as report

SERVER_DIR = Path(__file__).resolve().parent  # 어느 폴더에서 실행해도 server 기준으로 파일을 찾게


def main() -> None:
    parser = argparse.ArgumentParser(description="시연 데이터(리포트·보유 보험·추천·공백)를 한 번에 DB에 넣기")
    parser.add_argument("login_id", nargs="?", default="1234", help="데이터를 연결할 회원 아이디 (기본 1234)")
    parser.add_argument("--participant", default="hu4BE6F2", help="분석 결과 폴더의 참가자 코드 (기본 hu4BE6F2)")
    parser.add_argument("--output", default="new_try/portfolio_output", help="main_portfolio_design.py 결과 폴더")
    parser.add_argument("--existing", default="new_try/existing_insurance.csv", help="보유 보험 CSV")
    args = parser.parse_args()

    folder = SERVER_DIR / args.output / args.participant
    existing_csv = SERVER_DIR / args.existing

    # 1) 먼저 파일만 전부 읽고 검사한다. 여기서 틀리면 DB에는 접속도 하지 않음
    print(f"파일 확인 중: {folder.relative_to(SERVER_DIR)}")
    report_rows = report.read_rows(folder / "health_portfolio_long.csv")
    owned = existing.read_products(existing_csv)
    participant, recs = design.read_products(folder)
    gaps = design.read_gaps(folder, participant)

    # 세 파일이 같은 사람의 것인지 확인 (다른 사람 데이터가 섞여 들어가는 실수 방지)
    people = {
        "리포트": report_rows[0]["participant"],
        "보유 보험": owned[0]["participant"],
        "추천·공백": participant,
    }
    if len(set(people.values())) != 1 or participant != args.participant:
        raise ValueError(f"참가자 코드가 서로 다릅니다: {people} (지정: {args.participant})")

    # 2) 하나의 트랜잭션으로 저장
    from app.database import engine  # .env 를 읽어 DB 에 접속하는 서버의 engine

    with engine.begin() as conn:
        user_id = report.save_report(conn, args.login_id, report_rows)
        existing.save_products(conn, args.login_id, owned)
        design.save(conn, args.login_id, recs, gaps)

    print(f"\n저장 완료: {participant} → 회원 {args.login_id} (users.id={user_id})")
    print(f"  - 건강 리포트   질병 {len(report_rows)}개")
    print(f"  - 보유 보험     상품 {len(owned)}개 · 보장 {sum(len(p['coverages']) for p in owned)}개")
    print(f"  - 추천 보험     상품 {len(recs)}개 · 보장 {sum(len(p['coverages']) for p in recs)}개")
    print(f"  - 보장 공백     질병 {len(gaps)}개")


if __name__ == "__main__":
    try:
        main()
    except ValueError as e:  # 파일·데이터 문제, 회원 없음
        print(f"오류: {e}")
        sys.exit(1)
    except ProgrammingError as e:  # 테이블이 없음 (MySQL 1146) 등
        print(f"DB 오류: {e.orig}")
        print("→ 테이블이 없다면 Workbench에서 sql 폴더의 report.sql, existing_insurance.sql, insurance_design.sql 을 실행하세요.")
        sys.exit(1)
    except OperationalError as e:  # MySQL 이 꺼져 있거나 .env 접속 정보가 틀림
        print(f"DB 접속 오류: {e.orig}")
        print("→ MySQL 이 켜져 있는지, server/.env 의 DB_USER/DB_PASSWORD/DB_NAME 이 맞는지 확인하세요.")
        sys.exit(1)
