-- V8: 供应商档案域（K1）
-- 冻结设计：docs/platform-upgrade-design.md 4.1 V8__supplier_domain.sql
CREATE TABLE supplier (
    id varchar(32) PRIMARY KEY,
    name varchar(300) NOT NULL UNIQUE,
    contact_person varchar(100),
    phone varchar(50),
    email varchar(150),
    address varchar(500),
    main_categories varchar(500),          -- 主营品类，逗号分隔
    status varchar(20) NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','PAUSED','BLACKLISTED')),  -- 合作中/暂停/黑名单
    notes varchar(1000),
    created_at datetime(6) NOT NULL,
    updated_at datetime(6) NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;
