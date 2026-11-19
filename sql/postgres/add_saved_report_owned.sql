-- 이미 add_saved_report.sql 까지 실행해 둔 Supabase 에 "리포트에 넣은 기존 가입 보험" 테이블만 추가한다. SQL Editor 에서 한 번 Run.
-- (schema.sql 에도 같은 테이블이 들어 있으므로, DB 를 처음부터 새로 만들 때는 이 파일이 필요 없다)

-- 리포트에 함께 넣은 기존 가입 보험 (리포트 1 : N 기존 보험). (−)로 뺀 기존 보험은 들어가지 않음
CREATE TABLE IF NOT EXISTS user_saved_report_owned (
    report_id    INT      NOT NULL REFERENCES user_saved_report (id) ON DELETE CASCADE,
    insurance_id INT      NOT NULL REFERENCES user_insurance (id) ON DELETE CASCADE,
    sort_order   SMALLINT NOT NULL,
    PRIMARY KEY (report_id, insurance_id)
);

ALTER TABLE user_saved_report_owned ENABLE ROW LEVEL SECURITY;
