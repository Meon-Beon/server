-- DNA 검사 결제(토스페이먼츠)용 주문 테이블. MySQL Workbench에서 한 번만 실행
USE gene_insurance;

-- 결제 금액을 프론트에서 받은 값 그대로 믿지 않기 위해,
-- 결제창을 띄우기 전에 서버가 주문(금액 포함)을 먼저 저장해 둔다
CREATE TABLE dna_orders (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    order_id    VARCHAR(64)  NOT NULL UNIQUE,          -- 토스에 넘기는 주문번호
    user_id     INT          NULL,                     -- 비회원 결제면 NULL
    order_name  VARCHAR(100) NOT NULL,
    amount      INT          NOT NULL,                 -- 서버가 정한 결제 금액
    status      VARCHAR(10)  NOT NULL DEFAULT 'READY', -- READY / DONE / FAILED
    payment_key VARCHAR(200) NULL,                     -- 승인 후 토스가 준 결제 키
    method      VARCHAR(20)  NULL,                     -- 카드, 간편결제 등
    receipt_url VARCHAR(500) NULL,
    fail_reason VARCHAR(255) NULL,
    approved_at DATETIME     NULL,
    created_at  DATETIME     DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fk_dna_orders_user FOREIGN KEY (user_id) REFERENCES users (id)
);
