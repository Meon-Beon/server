-- 회원가입 기능용 DB 변경. MySQL Workbench에서 한 번만 실행
USE gene_insurance;

-- 회원가입 폼에서 받는 값을 저장할 컬럼 추가
-- (주민등록번호는 저장하지 않고 생년월일, 성별만 저장)
ALTER TABLE users
    ADD COLUMN birth_date     DATE         NULL,
    ADD COLUMN gender         CHAR(1)      NULL,  -- M / F
    ADD COLUMN phone          VARCHAR(11)  NULL,
    ADD COLUMN zipcode        VARCHAR(5)   NULL,
    ADD COLUMN address        VARCHAR(255) NULL,
    ADD COLUMN address_detail VARCHAR(255) NULL;

-- 이제 비밀번호를 해시로 비교하므로, 평문으로 넣어 둔 테스트 계정(1234 / 1234)의
-- 비밀번호를 해시 값으로 바꾼다. 로그인할 때 입력하는 비밀번호는 그대로 1234
UPDATE users
SET password = 'pbkdf2_sha256$600000$79effd27ecba3e5a51ec6e696407491e$fe3e66dd3b483fcc3fba16b80a414392c5b7f72a10946a48f04865f081751ebb',
    -- 테스트 계정 = 시연 인물 김재필 (건강검진 시연 데이터와 같은 값)
    name = '김재필', birth_date = '1981-09-26', gender = 'M', phone = '01012341234'
WHERE login_id = '1234';
