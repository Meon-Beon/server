-- 회원이 이미 가입한 보험(기존 계약) 테이블. MySQL Workbench에서 실행
-- 실제 가입 내역은 끌어올 수 없어서, 임의로 만든 new_try/existing_insurance.csv 를 넣어 시연한다
-- 테이블만 만든다. 데이터는 server 폴더에서 import_existing_insurance.py 로 넣는다
--   python import_existing_insurance.py "<existing_insurance.csv 경로>" <login_id>
-- 다시 실행하면 세 테이블을 지우고 새로 만든다 (들어 있던 데이터도 지워짐)
USE gene_insurance;

-- 자식 테이블부터 지워야 외래키 오류가 나지 않는다
-- (리포트에 넣은 기존 보험 기록도 이 테이블을 참조하므로 먼저 지움. insurance_design.sql 을 다시 실행하면 새로 생김)
DROP TABLE IF EXISTS user_saved_report_owned;
DROP TABLE IF EXISTS user_insurance_feature;
DROP TABLE IF EXISTS user_insurance_coverage;
DROP TABLE IF EXISTS user_insurance;

-- 가입 상품: 회원 한 명 × 상품 하나 = 한 행 (CSV 한 줄)
CREATE TABLE user_insurance (
    id            INT AUTO_INCREMENT PRIMARY KEY,
    user_id       INT          NOT NULL,           -- users.id
    participant   VARCHAR(20)  NOT NULL,           -- 분석 파일의 참가자 코드 (hu4BE6F2 등)
    sort_order    TINYINT      NOT NULL,           -- CSV에 적힌 순서 (화면 표시 순서)
    company_name  VARCHAR(30)  NOT NULL,           -- CSV: 회사명
    product_name  VARCHAR(100) NOT NULL,           -- CSV: 상품명
    premium       INT          NOT NULL,           -- CSV: 보험료 (월 보험료, 원)
    created_at    DATETIME     DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_user_insurance (user_id, company_name, product_name),
    CONSTRAINT fk_user_insurance_user FOREIGN KEY (user_id) REFERENCES users (id)
);

-- 보장: 상품 하나 × 보장 하나 = 한 행 (CSV 보장정보 JSON 배열의 원소 하나)
CREATE TABLE user_insurance_coverage (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    insurance_id    INT          NOT NULL,         -- user_insurance.id
    sort_order      TINYINT      NOT NULL,         -- 배열 순서
    coverage_name   VARCHAR(100) NOT NULL,         -- 보장명 (일반암진단 …)
    coverage_detail TEXT         NULL,             -- 보장내용 (지급 조건 설명)
    coverage_amount VARCHAR(50)  NOT NULL,         -- 보장금액, 원문 그대로 ("1000만원" 등 조건이 붙을 수 있어 문자열)
    CONSTRAINT fk_user_insurance_coverage FOREIGN KEY (insurance_id)
        REFERENCES user_insurance (id) ON DELETE CASCADE  -- 상품을 지우면 보장도 같이 지워짐
);

-- 특성: 상품 하나 × 특성 하나 = 한 행 (CSV 특성정보 JSON 배열의 원소 하나)
CREATE TABLE user_insurance_feature (
    id             INT AUTO_INCREMENT PRIMARY KEY,
    insurance_id   INT          NOT NULL,          -- user_insurance.id
    sort_order     TINYINT      NOT NULL,
    feature_name   VARCHAR(50)  NOT NULL,          -- 특성명 (예금자보호, 보험가격지수 …)
    feature_detail TEXT         NULL,              -- 특성내용
    CONSTRAINT fk_user_insurance_feature FOREIGN KEY (insurance_id)
        REFERENCES user_insurance (id) ON DELETE CASCADE
);
