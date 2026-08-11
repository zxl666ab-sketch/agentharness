-- 0.5.0 双 schema 初始化：业务 schema 由 Java Flyway 建表，运行时 schema 由 Python Agent 自建；
-- 本脚本只负责建库与授权，禁止交叉建表。
CREATE DATABASE IF NOT EXISTS caijiatai_business CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS caijiatai_runtime CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
GRANT ALL PRIVILEGES ON caijiatai_business.* TO 'caijiatai'@'%';
GRANT ALL PRIVILEGES ON caijiatai_runtime.* TO 'caijiatai'@'%';
FLUSH PRIVILEGES;