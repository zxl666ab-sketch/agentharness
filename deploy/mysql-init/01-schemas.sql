-- 0.5.0 双 schema 初始化：业务 schema 由 Java Flyway 建表，运行时 schema 由 Python Agent 自建。
-- 用户与授权由同目录的 02-users.sh 按运行时密钥创建，避免把凭据写入仓库。
CREATE DATABASE IF NOT EXISTS caijiatai_business CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE DATABASE IF NOT EXISTS caijiatai_runtime CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
