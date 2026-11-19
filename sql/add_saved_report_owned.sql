-- 로컬 MySQL 에 "리포트에 넣은 기존 가입 보험" 테이블만 추가한다. 한 번만 실행 (기존 리포트는 그대로 남음)
-- (insurance_design.sql 에도 같은 테이블이 들어 있으므로, 그 파일을 다시 실행할 때는 이 파일이 필요 없다)
USE gene_insurance;

-- 리포트에 함께 넣은 기존 가입 보험 (리포트 1 : N 기존 보험). 보험설계 화면에서 (−)로 뺀 기존 보험은 들어가지 않는다
-- 리포트를 지우거나 기존 보험을 다시 적재(import_existing_insurance.py)하면 함께 지워진다
CREATE TABLE IF NOT EXISTS user_saved_report_owned (
    report_id    INT     NOT NULL,                -- user_saved_report.id
    insurance_id INT     NOT NULL,                -- user_insurance.id
    sort_order   TINYINT NOT NULL,                -- 화면에 보이던 순서
    PRIMARY KEY (report_id, insurance_id),
    CONSTRAINT fk_user_saved_report_owned_report FOREIGN KEY (report_id)
        REFERENCES user_saved_report (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_saved_report_owned_ins FOREIGN KEY (insurance_id)
        REFERENCES user_insurance (id) ON DELETE CASCADE
);
