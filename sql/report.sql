-- 리포트(6번 건강 종합 리포트)용 질병별 위험도 테이블. MySQL Workbench에서 실행
-- 테이블만 만든다. 데이터는 server 폴더에서 import_report.py 로 넣는다
--   python import_report.py "<health_portfolio_long.csv 경로>" <login_id>
USE gene_insurance;

-- 회원 한 명 × 질병 하나 = 한 행
CREATE TABLE IF NOT EXISTS report_diseases (
    id               INT AUTO_INCREMENT PRIMARY KEY,
    user_id          INT          NOT NULL,
    participant      VARCHAR(20)  NOT NULL,           -- 분석 파일의 참가자 코드 (hu4BE6F2 등)
    disease          VARCHAR(20)  NOT NULL,           -- 당뇨, 심장, 뇌혈관 …
    sort_order       TINYINT      NOT NULL,           -- CSV에 적힌 순서 (동점일 때 마지막 기준)
    dna_risk         VARCHAR(10)  NOT NULL,           -- 원본 분류: 고위험군 / 위험군 / 일반군 / 평가불가
    dna_stage        TINYINT      NOT NULL,           -- 5단계 눈금: 고위험군 5, 위험군 3, 일반군·평가불가 1
    dna_weight       DECIMAL(4,3) NOT NULL,           -- 같은 단계일 때 순서를 정하는 가중치
    dna_summary      TEXT         NULL,               -- DNA로 본 근거 요약 (CSV: DNA)
    checkup_risk     VARCHAR(10)  NOT NULL,
    checkup_stage    TINYINT      NOT NULL,
    checkup_weight   DECIMAL(4,3) NOT NULL,
    checkup_summary  TEXT         NULL,               -- 건강검진으로 본 요약 (CSV: 검진)
    checkup_values   VARCHAR(255) NULL,               -- 검진 수치 (CSV: 검진수치)
    checkup_opinion  TEXT         NULL,               -- 판정 소견 (CSV: 판정소견)
    insurance_view   TEXT         NULL,               -- 보험 검토 관점 (CSV: 보험검토관점)
    created_at       DATETIME     DEFAULT CURRENT_TIMESTAMP,
    UNIQUE KEY uq_report_diseases_user (user_id, disease),
    CONSTRAINT fk_report_diseases_user FOREIGN KEY (user_id) REFERENCES users (id)
);
