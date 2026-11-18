-- 이미 schema.sql 을 실행해 둔 Supabase 에 "저장한 보험 설계" 테이블만 추가한다. SQL Editor 에서 한 번 Run.
-- (schema.sql 에도 같은 테이블이 들어 있으므로, DB 를 처음부터 새로 만들 때는 이 파일이 필요 없다)
CREATE TABLE IF NOT EXISTS user_saved_design (
    user_id           INT       NOT NULL REFERENCES users (id),
    recommendation_id INT       NOT NULL REFERENCES user_recommendation (id) ON DELETE CASCADE,
    sort_order        SMALLINT  NOT NULL,
    saved_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, recommendation_id)
);
ALTER TABLE user_saved_design ENABLE ROW LEVEL SECURITY;
