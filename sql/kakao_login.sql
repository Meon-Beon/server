-- 카카오 로그인용 users 테이블 변경. MySQL Workbench에서 한 번만 실행 (login.sql, signup.sql 다음)
USE gene_insurance;

ALTER TABLE users
    -- 카카오 회원번호. 같은 카카오 계정이 두 번 가입되지 않게 UNIQUE (NULL 은 여러 개 허용 → 일반 회원은 NULL)
    ADD COLUMN kakao_id BIGINT NULL UNIQUE AFTER login_id,
    -- 카카오 회원은 우리 사이트 비밀번호가 없으므로 NULL 허용
    MODIFY COLUMN password VARCHAR(255) NULL;

-- 카카오 회원의 login_id 는 서버가 "kakao_<회원번호>" 로 자동으로 만든다
