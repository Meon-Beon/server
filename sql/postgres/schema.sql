-- Supabase(PostgreSQL)용 전체 테이블. Supabase 대시보드 > SQL Editor 에 붙여 넣고 Run.
-- MySQL 용 sql/*.sql 9개(login, signup, kakao_login, dna_payment, health_checkup, report,
-- existing_insurance, insurance_design)를 PostgreSQL 문법으로 한 파일에 합친 것.
-- 보험상품 크롤링 테이블(insurance_products.sql)은 지금 API 에서 쓰지 않아 넣지 않았다.
--
-- MySQL → PostgreSQL 주요 차이
--   INT AUTO_INCREMENT      → INT GENERATED ALWAYS AS IDENTITY
--   TINYINT                 → SMALLINT          (PostgreSQL 에는 TINYINT 가 없음)
--   DATETIME                → TIMESTAMP
--   JSON                    → JSONB             (검색이 빠른 이진 JSON)
--   INDEX 를 CREATE TABLE 안에 → 따로 CREATE INDEX
--   USE gene_insurance;     → 없음 (Supabase 는 postgres 데이터베이스 하나를 씀)
--
-- 다시 실행하면 모든 테이블을 지우고 새로 만든다 (데이터도 지워짐) → 다시 넣으려면 python seed_demo.py

DROP TABLE IF EXISTS user_saved_design, user_recommendation_coverage, user_recommendation, user_coverage_gap,
    user_insurance_feature, user_insurance_coverage, user_insurance,
    report_diseases, health_checkups, dna_orders, users CASCADE;

-- ---------- 회원 (login.sql + signup.sql + kakao_login.sql) ----------
CREATE TABLE users (
    id             INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    login_id       VARCHAR(50)  NOT NULL UNIQUE,
    kakao_id       BIGINT       NULL UNIQUE,          -- 카카오 회원번호 (일반 회원은 NULL)
    password       VARCHAR(255) NULL,                 -- 카카오 회원은 NULL
    name           VARCHAR(50),
    birth_date     DATE         NULL,
    gender         CHAR(1)      NULL,                 -- M / F
    phone          VARCHAR(11)  NULL,
    zipcode        VARCHAR(5)   NULL,
    address        VARCHAR(255) NULL,
    address_detail VARCHAR(255) NULL,
    created_at     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

-- 테스트 계정 1234 / 1234 = 시연 인물 김재필 (비밀번호는 해시로 저장. 로그인 때 입력하는 값은 1234)
-- 생년월일·성별·휴대폰은 건강검진 시연 데이터(health_checkups_data.sql)와 같은 값
INSERT INTO users (login_id, password, name, birth_date, gender, phone)
VALUES ('1234', 'pbkdf2_sha256$600000$79effd27ecba3e5a51ec6e696407491e$fe3e66dd3b483fcc3fba16b80a414392c5b7f72a10946a48f04865f081751ebb',
        '김재필', '1981-09-26', 'M', '01012341234');

-- ---------- DNA 검사 결제 주문 (dna_payment.sql) ----------
CREATE TABLE dna_orders (
    id          INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    order_id    VARCHAR(64)  NOT NULL UNIQUE,
    user_id     INT          NULL REFERENCES users (id),
    order_name  VARCHAR(100) NOT NULL,
    amount      INT          NOT NULL,
    status      VARCHAR(10)  NOT NULL DEFAULT 'READY', -- READY / DONE / FAILED
    payment_key VARCHAR(200) NULL,
    method      VARCHAR(20)  NULL,
    receipt_url VARCHAR(500) NULL,
    fail_reason VARCHAR(255) NULL,
    approved_at TIMESTAMP    NULL,
    created_at  TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);

-- ---------- 건강검진 내역 (health_checkup.sql. 시연 데이터는 health_checkups_data.sql) ----------
CREATE TABLE health_checkups (
    id           INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name         VARCHAR(50)  NOT NULL,
    birth_date   DATE         NOT NULL,
    gender       CHAR(1)      NOT NULL,
    phone        VARCHAR(11)  NOT NULL,
    checkup_date DATE         NOT NULL,
    hospital     VARCHAR(100) NOT NULL,
    result       JSONB        NOT NULL,
    created_at   TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_health_checkups_person ON health_checkups (name, birth_date, gender, phone);

-- ---------- 건강 종합 리포트 (report.sql) ----------
CREATE TABLE report_diseases (
    id               INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          INT          NOT NULL REFERENCES users (id),
    participant      VARCHAR(20)  NOT NULL,
    disease          VARCHAR(20)  NOT NULL,
    sort_order       SMALLINT     NOT NULL,
    dna_risk         VARCHAR(10)  NOT NULL,
    dna_stage        SMALLINT     NOT NULL,
    dna_weight       DECIMAL(4,3) NOT NULL,
    dna_summary      TEXT         NULL,
    checkup_risk     VARCHAR(10)  NOT NULL,
    checkup_stage    SMALLINT     NOT NULL,
    checkup_weight   DECIMAL(4,3) NOT NULL,
    checkup_summary  TEXT         NULL,
    checkup_values   VARCHAR(255) NULL,
    checkup_opinion  TEXT         NULL,
    insurance_view   TEXT         NULL,
    created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_report_diseases_user UNIQUE (user_id, disease)
);

-- ---------- 보유 보험 (existing_insurance.sql) ----------
CREATE TABLE user_insurance (
    id            INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id       INT          NOT NULL REFERENCES users (id),
    participant   VARCHAR(20)  NOT NULL,
    sort_order    SMALLINT     NOT NULL,
    company_name  VARCHAR(30)  NOT NULL,
    product_name  VARCHAR(100) NOT NULL,
    premium       INT          NOT NULL,
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_user_insurance UNIQUE (user_id, company_name, product_name)
);

CREATE TABLE user_insurance_coverage (
    id              INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    insurance_id    INT          NOT NULL REFERENCES user_insurance (id) ON DELETE CASCADE,
    sort_order      SMALLINT     NOT NULL,
    coverage_name   VARCHAR(100) NOT NULL,
    coverage_detail TEXT         NULL,
    coverage_amount VARCHAR(50)  NOT NULL
);

CREATE TABLE user_insurance_feature (
    id             INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    insurance_id   INT          NOT NULL REFERENCES user_insurance (id) ON DELETE CASCADE,
    sort_order     SMALLINT     NOT NULL,
    feature_name   VARCHAR(50)  NOT NULL,
    feature_detail TEXT         NULL
);

-- ---------- 추천 보험 · 보장 공백 (insurance_design.sql) ----------
CREATE TABLE user_recommendation (
    id               INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id          INT          NOT NULL REFERENCES users (id),
    participant      VARCHAR(20)  NOT NULL,
    rec_rank         SMALLINT     NOT NULL,
    company_name     VARCHAR(30)  NOT NULL,
    product_name     VARCHAR(100) NOT NULL,
    insurer_type     VARCHAR(10)  NULL,
    product_type     VARCHAR(10)  NULL,
    premium          INT          NULL,
    premium_basis    VARCHAR(100) NULL,
    join_age         VARCHAR(20)  NULL,
    target_diseases  VARCHAR(50)  NULL,
    reason           TEXT         NULL,
    checks           TEXT         NULL,
    fit_status       VARCHAR(100) NULL,
    join_url         VARCHAR(500) NULL,
    created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_user_recommendation UNIQUE (user_id, company_name, product_name)
);

CREATE TABLE user_recommendation_coverage (
    id                INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    recommendation_id INT          NOT NULL REFERENCES user_recommendation (id) ON DELETE CASCADE,
    sort_order        SMALLINT     NOT NULL,
    coverage_name     VARCHAR(100) NOT NULL,
    coverage_detail   TEXT         NULL,
    coverage_amount   VARCHAR(100) NULL
);

CREATE TABLE user_coverage_gap (
    id              INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id         INT          NOT NULL REFERENCES users (id),
    participant     VARCHAR(20)  NOT NULL,
    sort_order      SMALLINT     NOT NULL,
    disease         VARCHAR(20)  NOT NULL,
    at_risk         BOOLEAN      NOT NULL,
    diagnosed       VARCHAR(10)  NULL,
    status          VARCHAR(10)  NOT NULL,
    current_summary TEXT         NULL,
    gap_detail      TEXT         NULL,
    created_at      TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_user_coverage_gap UNIQUE (user_id, disease)
);

-- ---------- 저장한 보험 설계 (회원당 1개, 저장하면 덮어씀. 마이페이지에서 보여 줌) ----------
CREATE TABLE user_saved_design (
    user_id           INT       NOT NULL REFERENCES users (id),
    recommendation_id INT       NOT NULL REFERENCES user_recommendation (id) ON DELETE CASCADE,
    sort_order        SMALLINT  NOT NULL,
    saved_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, recommendation_id)
);

-- ---------- 보안: Supabase 공개 API(Data API) 로 테이블이 노출되지 않게 ----------
-- Supabase 는 public 스키마 테이블을 브라우저용 REST API 로 자동 공개한다.
-- RLS(행 단위 보안)를 켜고 정책을 하나도 만들지 않으면 그 API 로는 아무것도 읽고 쓸 수 없다.
-- 우리 FastAPI 서버는 DB 비밀번호(postgres 계정)로 직접 접속하므로 RLS 의 영향을 받지 않는다.
ALTER TABLE users                        ENABLE ROW LEVEL SECURITY;
ALTER TABLE dna_orders                   ENABLE ROW LEVEL SECURITY;
ALTER TABLE health_checkups              ENABLE ROW LEVEL SECURITY;
ALTER TABLE report_diseases              ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_insurance               ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_insurance_coverage      ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_insurance_feature       ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_recommendation          ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_recommendation_coverage ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_coverage_gap            ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_saved_design            ENABLE ROW LEVEL SECURITY;
