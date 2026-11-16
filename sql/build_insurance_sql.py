"""크롤링 CSV 6개 → MySQL용 insurance_products.sql 생성.

사용법 (server/sql 폴더에서):
    python build_insurance_sql.py ../..        # CSV가 있는 폴더 경로
→ 같은 폴더에 insurance_products.sql 생성 → MySQL Workbench에서 실행.

생성된 SQL은 보험상품 테이블 4개를 DROP 후 다시 만든다(재수집 시 그대로 다시 실행).
users, dna_orders 등 다른 테이블은 건드리지 않는다.
"""
from __future__ import annotations

import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

GENDER = {'남': 'M', '여': 'F'}
PERIOD = {'보험기간 10년 이상 (장기)': '장기', '보험기간 10년 미만 (단기)': '단기'}

SCHEMA = """\
-- 보험상품(실손의료/암/어린이) 테이블. MySQL Workbench에서 실행
-- build_insurance_sql.py가 크롤링 CSV로 자동 생성한 파일 (생성: {generated})
-- 다시 실행하면 보험상품 테이블 4개와 뷰를 지우고 새로 만든다.
USE gene_insurance;

DROP VIEW IF EXISTS v_avg_premium;
DROP TABLE IF EXISTS insurance_coverage;
DROP TABLE IF EXISTS insurance_feature;
DROP TABLE IF EXISTS insurance_premium;
DROP TABLE IF EXISTS insurance_product;

-- 상품 마스터: 상품 하나당 한 행
CREATE TABLE insurance_product (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    category       ENUM('실손','암','어린이') NOT NULL,
    product_code   VARCHAR(30)  NOT NULL,          -- 보험다모아 상품코드
    company_name   VARCHAR(30)  NOT NULL,
    product_name   VARCHAR(100) NOT NULL,
    insurance_type VARCHAR(10)  NULL,              -- 생명보험 / 손해보험
    join_age       VARCHAR(20)  NULL,              -- 가입연령 (예: 0~60)
    join_method    VARCHAR(100) NULL,              -- 가입형태 (온라인, 전화 등)
    join_url       VARCHAR(500) NULL,
    remark         VARCHAR(255) NULL,              -- 비고/기타정보
    join_rule      TEXT         NULL,              -- 사이트의 가입기준 안내문
    source_url     VARCHAR(255) NULL,
    UNIQUE KEY uq_product (category, product_code)
);

-- 조건별 보험료: 실손/암은 성별·보험나이, 어린이는 보험기간 기준
CREATE TABLE insurance_premium (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    product_id       INT          NOT NULL,
    gender           CHAR(1)      NULL,            -- M / F (users.gender와 동일), 어린이는 NULL
    insurance_age    TINYINT UNSIGNED NULL,        -- 사이트 계산 보험나이, 어린이는 NULL
    renewal_type     VARCHAR(10)  NULL,            -- 암: 갱신형 / 비갱신형 (확인 못 하면 NULL)
    insurance_period VARCHAR(10)  NULL,            -- 어린이: 장기 / 단기
    premium          INT          NULL,            -- 월 보험료(원), 사이트에 금액이 없으면 NULL
    premium_display  VARCHAR(30)  NULL,            -- 사이트 표시 그대로
    join_coverage    TEXT         NULL,            -- 조회한 가입담보/선택보장 조건
    collected_at     DATETIME     NULL,
    CONSTRAINT fk_premium_product FOREIGN KEY (product_id)
        REFERENCES insurance_product (id) ON DELETE CASCADE,
    INDEX idx_premium_cond (gender, insurance_age)
);

-- 상품 특성 (예금자보호, 보험가격지수, 갱신형 여부 등): 특성 하나당 한 행
CREATE TABLE insurance_feature (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    product_id    INT           NOT NULL,
    seq           SMALLINT      NOT NULL,          -- 상품 안 순번
    feature_code  VARCHAR(10)   NULL,
    feature_name  VARCHAR(50)   NOT NULL,
    display_text  VARCHAR(100)  NULL,
    value         DECIMAL(10,2) NULL,              -- 보험가격지수 등 수치
    unit          VARCHAR(5)    NULL,
    description   TEXT          NULL,
    CONSTRAINT fk_feature_product FOREIGN KEY (product_id)
        REFERENCES insurance_product (id) ON DELETE CASCADE
);

-- 보장 내용 (암, 어린이): 보장 하나당 한 행
CREATE TABLE insurance_coverage (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    product_id      INT          NOT NULL,
    seq             SMALLINT     NOT NULL,
    coverage_name   VARCHAR(100) NOT NULL,
    pay_reason      TEXT         NULL,             -- 지급사유
    coverage_amount VARCHAR(100) NULL,             -- 보장금액 (예: 1000만원)
    CONSTRAINT fk_coverage_product FOREIGN KEY (product_id)
        REFERENCES insurance_product (id) ON DELETE CASCADE
);

-- 분류·성별·보험나이별 평균 보험료
-- 45세 남성: SELECT * FROM v_avg_premium WHERE gender='M' AND insurance_age=45;
CREATE VIEW v_avg_premium AS
SELECT p.category,
       pr.gender,
       pr.insurance_age,
       COUNT(*)                AS product_count,
       ROUND(AVG(pr.premium))  AS avg_premium,
       MIN(pr.premium)         AS min_premium,
       MAX(pr.premium)         AS max_premium
FROM insurance_product p
JOIN insurance_premium pr ON pr.product_id = p.id
WHERE pr.premium IS NOT NULL
GROUP BY p.category, pr.gender, pr.insurance_age;
"""


def blank(v):
    return v is None or (isinstance(v, float) and math.isnan(v)) or v is pd.NA or (isinstance(v, str) and not v.strip())


def q(v):
    """파이썬 값 → MySQL 리터럴."""
    if blank(v):
        return 'NULL'
    if isinstance(v, bool):
        return '1' if v else '0'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return str(int(v)) if v.is_integer() else repr(v)
    s = str(v).replace('\\', '\\\\').replace("'", "''")
    return f"'{s}'"


def s(v):
    return None if blank(v) else str(v).strip()


def num(v):
    if blank(v):
        return None
    try:
        return float(str(v).replace(',', ''))
    except ValueError:
        return None


def int_or_none(v):
    n = num(v)
    return None if n is None else int(n)


def dt(v):
    if blank(v):
        return None
    return datetime.fromisoformat(str(v)).strftime('%Y-%m-%d %H:%M:%S')


def insert(table, columns, rows, chunk=100):
    out = []
    for i in range(0, len(rows), chunk):
        values = ',\n'.join('(' + ', '.join(q(r[c]) for c in columns) + ')' for r in rows[i:i + chunk])
        out.append(f'INSERT INTO {table} ({", ".join(columns)}) VALUES\n{values};')
    return '\n'.join(out)


def read(folder, name):
    path = folder / name
    if not path.exists():
        raise FileNotFoundError(f'CSV가 없습니다: {path}')
    return pd.read_csv(path, encoding='utf-8-sig', dtype={'상품코드': str, '특성코드': str})


def build(folder: Path):
    products, premiums, features, coverages = [], [], [], []
    product_ids = {}      # (category, code) -> id
    run_to_product = {}   # (category, 조회결과ID) -> product id
    first_run = set()     # 특성/보장을 처음 받은 조회결과ID (같은 상품 중복 방지)

    def product_id(category, row, *, remark, method):
        key = (category, s(row['상품코드']))
        if key[1] is None:
            raise ValueError(f'{category}: 상품코드가 비어 있는 행이 있습니다.')
        if key not in product_ids:
            product_ids[key] = len(products) + 1
            products.append({
                'id': product_ids[key], 'category': category, 'product_code': key[1],
                'company_name': s(row['회사명']), 'product_name': s(row['상품명']),
                'insurance_type': s(row.get('보험구분')), 'join_age': s(row.get('가입연령')),
                'join_method': s(method), 'join_url': s(row.get('가입URL')),
                'remark': s(remark), 'join_rule': s(row.get('가입기준')),
                'source_url': s(row.get('출처URL')),
            })
            first_run.add((category, row['조회결과ID']))
        run_to_product[(category, row['조회결과ID'])] = product_ids[key]
        return product_ids[key]

    # 실손 / 암: 성별·보험나이 기준 보험료
    for category, prefix in (('실손', '실손의료'), ('암', '암')):
        wide = read(folder, f'{prefix}_product_wideform.csv')
        for _, row in wide.iterrows():
            pid = product_id(category, row, remark=row.get('기타정보'), method=row.get('가입형태'))
            renewal = s(row.get('갱신구분'))
            premiums.append({
                'product_id': pid, 'gender': GENDER.get(s(row['성별'])),
                'insurance_age': int_or_none(row['보험나이']),
                'renewal_type': renewal if renewal in ('갱신형', '비갱신형') else None,
                'insurance_period': None, 'premium': int_or_none(row['보험료']),
                'premium_display': s(row.get('보험료_표시')),
                'join_coverage': s(row.get('가입담보')) or s(row.get('선택보장')),
                'collected_at': dt(row.get('조회일시')),
            })
            if category == '암' and (category, row['조회결과ID']) in first_run and not blank(row.get('보장상세')):
                for i, item in enumerate(json.loads(row['보장상세']), 1):
                    if s(item.get('보장내용')):
                        coverages.append({'product_id': pid, 'seq': i, 'coverage_name': s(item['보장내용']),
                                          'pay_reason': s(item.get('지급사유')),
                                          'coverage_amount': s(item.get('보장금액'))})
        long = read(folder, f'{prefix}_product_longform.csv')
        for _, row in long.iterrows():
            key = (category, row['조회결과ID'])
            if key not in run_to_product:
                raise ValueError(f'{prefix} longform의 조회결과ID가 wideform에 없습니다: {row["조회결과ID"]}')
            if key in first_run:
                features.append(feature_row(run_to_product[key], row, int_or_none(row['특성순번'])))

    # 어린이: 성별·나이 없이 보험기간(장기/단기) 기준
    wide = read(folder, '어린이_product_wideform.csv')
    for _, row in wide.iterrows():
        pid = product_id('어린이', row, remark=row.get('기타사항'), method=row.get('가입형태'))
        premiums.append({
            'product_id': pid, 'gender': None, 'insurance_age': None, 'renewal_type': None,
            'insurance_period': PERIOD.get(s(row['보험기간']), s(row['보험기간'])),
            'premium': int_or_none(row['보험료']), 'premium_display': s(row.get('보험료_표시')),
            'join_coverage': None, 'collected_at': dt(row.get('수집시각')),
        })
    long = read(folder, '어린이_product_longform.csv')
    seq = {}
    for _, row in long.iterrows():
        key = ('어린이', row['조회결과ID'])
        if key not in run_to_product:
            raise ValueError(f'어린이 longform의 조회결과ID가 wideform에 없습니다: {row["조회결과ID"]}')
        if key not in first_run:
            continue
        pid = run_to_product[key]
        kind = row['상세구분']
        seq[(pid, kind)] = seq.get((pid, kind), 0) + 1
        if kind == '보장':
            coverages.append({'product_id': pid, 'seq': seq[(pid, kind)], 'coverage_name': s(row['보장명']),
                              'pay_reason': s(row['지급사유']), 'coverage_amount': s(row['보장금액'])})
        elif kind == '특성':
            features.append(feature_row(pid, row, seq[(pid, kind)]))

    generated = datetime.now().strftime('%Y-%m-%d %H:%M')
    parts = [SCHEMA.format(generated=generated), '-- ===== 데이터 =====', 'START TRANSACTION;',
             insert('insurance_product', ['id', 'category', 'product_code', 'company_name', 'product_name',
                    'insurance_type', 'join_age', 'join_method', 'join_url', 'remark', 'join_rule',
                    'source_url'], products),
             insert('insurance_premium', ['product_id', 'gender', 'insurance_age', 'renewal_type',
                    'insurance_period', 'premium', 'premium_display', 'join_coverage', 'collected_at'], premiums),
             insert('insurance_feature', ['product_id', 'seq', 'feature_code', 'feature_name', 'display_text',
                    'value', 'unit', 'description'], features),
             insert('insurance_coverage', ['product_id', 'seq', 'coverage_name', 'pay_reason',
                    'coverage_amount'], coverages),
             'COMMIT;', '']
    counts = {'상품': len(products), '보험료': len(premiums), '특성': len(features), '보장': len(coverages)}
    return '\n\n'.join(parts), counts


def feature_row(pid, row, seq):
    return {'product_id': pid, 'seq': seq, 'feature_code': s(row.get('특성코드')),
            'feature_name': s(row['특성명']), 'display_text': s(row.get('표시내용')),
            'value': num(row.get('수치')), 'unit': s(row.get('단위')),
            'description': s(row.get('설명'))}


if __name__ == '__main__':
    here = Path(__file__).resolve().parent
    csv_folder = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else here.parent.parent
    sql, counts = build(csv_folder)
    target = here / 'insurance_products.sql'
    target.write_text(sql, encoding='utf-8')
    print(f'CSV 폴더: {csv_folder}')
    print('생성 행 수:', ', '.join(f'{k} {v}' for k, v in counts.items()))
    print(f'저장: {target}')
