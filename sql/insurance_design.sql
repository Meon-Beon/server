-- 보험설계(7-1) 화면용: 회원별 추천 보험 + 보장 공백 점검 결과 테이블. MySQL Workbench에서 실행
-- main_portfolio_design.py 결과 폴더(portfolio_output/<참가자>)의 CSV 3개를 넣는다
--   추천보험_longform.csv        → 추천 상품과 보장 목록 (보장 1개 = 1행)
--   insurance_recommendations.csv → 상품별 추천순위·보험료·대상질병·추천이유 (longform 에 없는 값만 가져옴)
--   insurance_coverage_gaps.csv   → 질병별 보장 공백 점검
--   ../../보험상품_wideform.csv     → 추천 상품의 가입URL (회사명+상품명으로 찾음)
-- 테이블만 만든다. 데이터는 server 폴더에서 import_insurance_design.py 로 넣는다
--   python import_insurance_design.py "<결과 폴더 경로>" <login_id>
-- 다시 실행하면 세 테이블을 지우고 새로 만든다 (들어 있던 데이터도 지워짐)
USE gene_insurance;

DROP TABLE IF EXISTS user_saved_design;            -- 추천 상품을 참조하므로 가장 먼저
DROP TABLE IF EXISTS user_recommendation_coverage;
DROP TABLE IF EXISTS user_recommendation;
DROP TABLE IF EXISTS user_coverage_gap;

-- 추천 상품: 회원 한 명 × 추천 상품 하나 = 한 행
CREATE TABLE user_recommendation (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    user_id          INT          NOT NULL,          -- users.id
    participant      VARCHAR(20)  NOT NULL,          -- 분석 파일의 참가자 코드 (hu4BE6F2 등)
    rec_rank         TINYINT      NOT NULL,          -- 추천순위 (1이 가장 추천). rank 는 MySQL 예약어라 rec_rank
    company_name     VARCHAR(30)  NOT NULL,
    product_name     VARCHAR(100) NOT NULL,
    insurer_type     VARCHAR(10)  NULL,              -- 보험구분: 생명 / 손해
    product_type     VARCHAR(10)  NULL,              -- 보험종류: 질병 / 상해
    premium          INT          NULL,              -- 월 보험료(원). 예시 보험료라 없을 수 있음
    premium_basis    VARCHAR(100) NULL,              -- 보험료기준 (본인 견적 아님 등)
    join_age         VARCHAR(20)  NULL,              -- 가입연령 (19~60)
    target_diseases  VARCHAR(50)  NULL,              -- 대상질병, "심장 / 뇌혈관" 처럼 / 로 구분
    reason           TEXT         NULL,              -- 추천이유
    checks           TEXT         NULL,              -- 확인필요사항
    fit_status       VARCHAR(100) NULL,              -- 적합성상태
    join_url         VARCHAR(500) NULL,              -- 가입 페이지 주소 (new_try/보험상품_wideform.csv 의 가입URL, 없으면 NULL)
    created_at       DATETIME     DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_recommendation (user_id, company_name, product_name),
    CONSTRAINT fk_user_recommendation_user FOREIGN KEY (user_id) REFERENCES users (id)
);

-- 추천 상품의 보장: 상품 하나 × 보장 하나 = 한 행 (추천보험_longform.csv 한 줄)
CREATE TABLE user_recommendation_coverage (
    id                INT AUTO_INCREMENT PRIMARY KEY,
    recommendation_id INT          NOT NULL,         -- user_recommendation.id
    sort_order        SMALLINT     NOT NULL,         -- CSV에 적힌 순서
    coverage_name     VARCHAR(100) NOT NULL,         -- 보장명
    coverage_detail   TEXT         NULL,             -- 보장내용
    coverage_amount   VARCHAR(100) NULL,             -- 보장금액, 원문 그대로 ("3일초과 1일당 1만원 …" 도 있음)
    CONSTRAINT fk_user_recommendation_coverage FOREIGN KEY (recommendation_id)
        REFERENCES user_recommendation (id) ON DELETE CASCADE
);

-- 보장 공백 점검: 회원 한 명 × 질병 하나 = 한 행 (insurance_coverage_gaps.csv 한 줄)
CREATE TABLE user_coverage_gap (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    user_id         INT          NOT NULL,
    participant     VARCHAR(20)  NOT NULL,
    sort_order      TINYINT      NOT NULL,
    disease         VARCHAR(20)  NOT NULL,           -- 당뇨, 심장, 뇌혈관 …
    at_risk         BOOLEAN      NOT NULL,           -- 잠재위험대상 (예 → 1)
    diagnosed       VARCHAR(10)  NULL,               -- 진단여부: 예 / 아니오 / 미확인
    status          VARCHAR(10)  NOT NULL,           -- 점검상태: 공백의심 / 보장확인 / 정보부족
    current_summary TEXT         NULL,               -- 현재보장요약
    gap_detail      TEXT         NULL,               -- 공백또는확인사항
    created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_coverage_gap (user_id, disease),
    CONSTRAINT fk_user_coverage_gap_user FOREIGN KEY (user_id) REFERENCES users (id)
);

-- 회원이 보험설계 화면에서 "설계 저장"한 추천 상품 (회원당 설계 1개, 저장하면 덮어씀)
-- 마이페이지 > 보험 설계 보러가기 에서 보여 준다
-- 추천 상품을 다시 적재(import_insurance_design.py / seed_demo.py)하면 CASCADE 로 저장한 설계도 지워진다
CREATE TABLE user_saved_design (
    user_id           INT      NOT NULL,          -- users.id
    recommendation_id INT      NOT NULL,          -- user_recommendation.id
    sort_order        TINYINT  NOT NULL,          -- 설계 칸에 담은 순서
    saved_at          DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, recommendation_id),
    CONSTRAINT fk_user_saved_design_user FOREIGN KEY (user_id) REFERENCES users (id),
    CONSTRAINT fk_user_saved_design_rec FOREIGN KEY (recommendation_id)
        REFERENCES user_recommendation (id) ON DELETE CASCADE
);
