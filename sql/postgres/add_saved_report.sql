-- 이미 schema.sql 을 실행해 둔 Supabase 를 "저장한 보험 설계 리포트(회원당 여러 개)" 구조로 바꾼다. SQL Editor 에서 한 번 Run.
-- 예전 "회원당 설계 1개" 테이블(user_saved_design)과 그 데이터는 지운다 (시연 데이터라 옮기지 않음)
-- (schema.sql 에도 같은 테이블이 들어 있으므로, DB 를 처음부터 새로 만들 때는 이 파일이 필요 없다)
DROP TABLE IF EXISTS user_saved_design;

CREATE TABLE IF NOT EXISTS user_saved_report (
    id       INT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    user_id  INT       NOT NULL REFERENCES users (id),
    saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_user_saved_report_user ON user_saved_report (user_id, saved_at);

CREATE TABLE IF NOT EXISTS user_saved_report_item (
    report_id         INT      NOT NULL REFERENCES user_saved_report (id) ON DELETE CASCADE,
    recommendation_id INT      NOT NULL REFERENCES user_recommendation (id) ON DELETE CASCADE,
    sort_order        SMALLINT NOT NULL,
    PRIMARY KEY (report_id, recommendation_id)
);

ALTER TABLE user_saved_report      ENABLE ROW LEVEL SECURITY;
ALTER TABLE user_saved_report_item ENABLE ROW LEVEL SECURITY;
