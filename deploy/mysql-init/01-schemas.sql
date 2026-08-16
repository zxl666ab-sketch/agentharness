-- Java business schema. The Agent Runtime persists under AGENTHARNESS_DATA_DIR.
-- 用户与授权由同目录的 02-users.sh 按运行时密钥创建，避免把凭据写入仓库。
CREATE DATABASE IF NOT EXISTS caijiatai_business CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
