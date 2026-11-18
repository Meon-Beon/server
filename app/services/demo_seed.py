"""시연 인물로 가입하면 시연 데이터(리포트·보유 보험·추천·공백)를 자동으로 넣어 준다.

시연 데이터는 실제 가입 내역·분석 결과를 끌어올 수 없어서 미리 만든 파일(new_try)이고,
그 주인공인 김재필(1981-09-26, 남)과 이름·생년월일·성별이 같은 사람이 가입했을 때만 연결한다.
실제 저장은 server/seed_demo.py 의 함수를 그대로 쓴다 (명령으로 넣을 때와 같은 결과)
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

from sqlalchemy import Connection

DEMO_NAME = "김재필"
DEMO_BIRTH = date(1981, 9, 26)
DEMO_GENDER = "M"
DEMO_PARTICIPANT = "hu4BE6F2"

# seed_demo.py 와 import_*.py 는 app 패키지 밖(server 폴더 바로 아래)에 있으므로 import 경로에 추가
SERVER_DIR = Path(__file__).resolve().parents[2]
if str(SERVER_DIR) not in sys.path:
    sys.path.append(str(SERVER_DIR))


def is_demo_person(name: str, birth_date: date, gender: str) -> bool:
    return name.replace(" ", "") == DEMO_NAME and birth_date == DEMO_BIRTH and gender == DEMO_GENDER


def seed_if_demo(db: Connection, login_id: str, name: str, birth_date: date, gender: str) -> bool:
    """시연 인물이면 데이터를 넣고 True. 실패해도 가입은 이미 끝났으므로 오류를 밖으로 던지지 않는다"""
    if not is_demo_person(name, birth_date, gender):
        return False
    try:
        import seed_demo  # 처음 쓸 때만 불러옴 (일반 가입에는 필요 없음)

        data = seed_demo.load_demo_data(DEMO_PARTICIPANT)
        seed_demo.save_demo_data(db, login_id, data)
        db.commit()
        print(f"시연 데이터 연결: {DEMO_PARTICIPANT} → {login_id}")
        return True
    except Exception as e:  # 파일 없음·테이블 없음 등. 회원 가입 자체는 성공으로 둔다
        db.rollback()
        print(f"시연 데이터 연결 실패({login_id}): {e!r}")
        return False
