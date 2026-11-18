-- 스키마 생성
CREATE DATABASE gene_insurance
  DEFAULT CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE gene_insurance;

-- 테이블 생성
CREATE TABLE users (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    login_id   VARCHAR(50)  NOT NULL UNIQUE,
    password   VARCHAR(255) NOT NULL,
    name       VARCHAR(50),
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

-- 테스트 계정 넣기
INSERT INTO users (login_id, password, name)
VALUES ('1234', '1234', '김재필');  -- 시연 인물

-- 로그인 확인 쿼리
SELECT id, login_id, name
FROM users
WHERE login_id = '1234'
  AND password = '1234';