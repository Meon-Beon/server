-- 로컬 MySQL 을 "저장한 보험 설계 리포트(회원당 여러 개)" 구조로 바꾼다. 한 번만 실행.
-- 예전 "회원당 설계 1개" 테이블(user_saved_design)과 그 데이터는 지운다 (시연 데이터라 옮기지 않음)
-- (insurance_design.sql 에도 같은 테이블이 들어 있으므로, 그 파일을 다시 실행할 때는 이 파일이 필요 없다)
USE gene_insurance;

DROP TABLE IF EXISTS user_saved_design;

CREATE TABLE IF NOT EXISTS user_saved_report (
    id       INT AUTO_INCREMENT PRIMARY KEY,
    user_id  INT      NOT NULL,
    saved_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    KEY idx_user_saved_report_user (user_id, saved_at),
    CONSTRAINT fk_user_saved_report_user FOREIGN KEY (user_id) REFERENCES users (id)
);

CREATE TABLE IF NOT EXISTS user_saved_report_item (
    report_id         INT     NOT NULL,
    recommendation_id INT     NOT NULL,
    sort_order        TINYINT NOT NULL,
    PRIMARY KEY (report_id, recommendation_id),
    CONSTRAINT fk_user_saved_report_item_report FOREIGN KEY (report_id)
        REFERENCES user_saved_report (id) ON DELETE CASCADE,
    CONSTRAINT fk_user_saved_report_item_rec FOREIGN KEY (recommendation_id)
        REFERENCES user_recommendation (id) ON DELETE CASCADE
);
