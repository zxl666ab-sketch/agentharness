# 智采 Agent（采价台）改造规格书

- 文档版本：v1.1（含评审修订）
- 日期：2026-08-11
- 目标版本：0.5.0
- 基线提交：`4bcea7f`（`codex/java-python-agent-repair`「减缩版1」）
- 开发分支：`codex/java-python-mysql-rag`（自 `4bcea7f` 新建）
- 状态：已评审待实施

## 1. 背景与目标

简历第二个项目「智采 Agent」即本仓库（`agentharness`）。当前主线是 Python 单体（FastAPI + SQLite + RAG），功能可用、测试绿色；本次改造**不是为了修 bug，而是为了求职含金量**：

- 把项目改造成 **Java 写业务（4 成）+ Python 写智能体（6 成）** 的双栈形态，匹配“AI 应用 / Agent 开发 + Java 后端”岗位。
- Java 作为业务主机，先写完**完整业务**并可独立运行；Python Agent 作为**嵌入式微服务**通过 **RabbitMQ（唯一通道）** 接入。
- 保留并迁移现有 Python 侧的高价值资产：文档解析与冻结评测（617/620、31/31、0/17）、22 类异常回归、审计与断点续跑能力。

## 2. 非目标（明确不做）

- 不重写 LangGraph（沿用现有自定义引擎）；不引入 MCP。
- 不引入 Seata/2PC 分布式事务（用 outbox + 幂等 + generation/version 校验）。
- 不引入网关/注册中心/负载均衡/Redis 分布式锁/Kafka/RocketMQ/Neo4j。
- 不做高 QPS 实现；仅保留 `docs/architecture.md` 中的“扩容路径”说明（v1 为本地单用户形态）。
- RAG（Milvus）为二期，不在 v1 必交付范围。

## 3. 基线现状与起点

- `master`（`7915823`）与 `4bcea7f` 在 `1e4322f` 分叉；`4bcea7f` 已包含一版 Java+Python 拆分（Spring Boot 4.1 / PostgreSQL 17 / SQLite / HTTP 内部接口）。
- 实测：`4bcea7f` Java 可编译，12/13 测试通过（唯一失败为本机 Docker 未启动的 Testcontainers 集成测试）；黄金契约一致。
- 现状通信为 HTTP + Token + 反向代理，本规格将其替换为 MQ-only。
- 合流策略：**不合并回 master**；完成后把新分支提升为新 `master`，旧主线归档为 `legacy/python-monolith`（避免 31 个冲突文件的结构性合并）。

## 4. 最终目标架构

### 4.1 服务拓扑

```mermaid
flowchart LR
    Browser["React 工作台"] -->|"HTTP/SSE 仅 127.0.0.1:8741"| Java["Java 业务主机（Spring Boot）"]
    Java <-->|"RabbitMQ（唯一通道）"| MQ["RabbitMQ"]
    MQ <-->|"commands/results/rpc/events"| Py["Python Agent 微服务（FastAPI，仅内网 8742）"]
    Py -->|"外呼"| LLM["LLM API"]
    Py <-->|"二期"| VDB["Milvus"]
    Java -->|"业务真源"| MySQL
    Py -->|"运行时真源"| MySQL
    Java <--> Redis
    Py <--> Redis
```

### 4.2 职责边界（Java 4 / Python 6）

| Java 业务主机（4 成，先完成、可独立跑） | Python Agent 微服务（6 成，主体，后嵌入） |
|---|---|
| 任务/附件/报价/人工修正/比价快照 | 自然语言需求结构化 + XLSX/PDF 文档解析 |
| 确定性比价（BigDecimal）/资格淘汰/稳定排序 | 阶段状态机 + 工具编排 + 人工复核/审批 HITL |
| 待决审批/正式决定/审计/报告/PO/确认邮件 | Provider/模型调用 + 离线 fake + 预算/重试/恢复 |
| Web 静态托管 + SSE 入口 | 运行时 MySQL + 断点续跑/幂等 |
| MySQL `caijiatai_business`（Flyway） | 评测（617/620 + 22 类异常回归） |
| Redis 任务上下文缓存 | RAG（二期）+ Redis Run/RAG 上下文缓存 |
| MQ：outbox relay/结果消费/RPC 服务/事件投影/心跳 | MQ：命令消费/结果发布/RPC 客户端/事件发布/心跳 |

### 4.3 业务闭环（保持不变）

建需求（数量/规格/公差/交期/预算/汇率）→ 导入多家 XLSX/PDF 报价（原件 SHA-256 Artifact Store）→ Agent 解析+结构化（低置信度强制人工复核）→ 确定性比价（硬约束淘汰 → Decimal 到货成本排序 → 不可变快照）→ 一次性人工审批（绑定 Run/快照/输入哈希，stale 防护）→ 审计报告 + PO/供应商确认邮件（证据哈希可复现）。

## 5. 通信设计（MQ-only，RabbitMQ）

### 5.1 队列拓扑

| 队列 | 方向 | 用途 |
|---|---|---|
| `caijiatai.commands` | Java→Python | 异步命令：start_conversation / import_quote / analyze / approve_decision / reopen_task |
| `caijiatai.results` | Python→Java | 命令结果回传 |
| `caijiatai.rpc.requests` | Python→Java | 同步 RPC：get_task_context / get_artifact / list_history_facts |
| `caijiatai.rpc.responses` | Java→Python | RPC 响应（correlationId 匹配） |
| `caijiatai.events` | Python→Java | 运行时事件/报告投影/心跳（`run.*`、`report.*`、`tool.*`、`heartbeat.ping`） |

- 每个队列配 DLQ：`*.dlq`；exchange 为 `caijiatai.exchange`（direct）+ `caijiatai.events`（topic）；全部 quorum queue。
- 统一 vhost `caijiatai`；两个用户：`java-svc`（发布 commands、消费 results/rpc.requests/events）、`python-agent`（消费 commands、发布 results/rpc.requests/events）——按队列读写最小权限授权。
- RabbitMQ 配置 `max_message_size = 16MB`（覆盖 Artifact 分块后单消息上限）。

### 5.2 消息 schema（示例）

命令消息（Java→Python）：
```json
{
  "operation_id": "uuid",
  "operation_type": "analyze",
  "aggregate_id": "32hex",
  "generation": 3,
  "expected_task_version": 2,
  "payload_sha256": "64hex",
  "payload": {},
  "published_at": "2026-08-11T00:00:00Z",
  "signature": "hmac-sha256-hex"
}
```

结果消息（Python→Java）：
```json
{
  "operation_id": "uuid",
  "aggregate_id": "32hex",
  "generation": 3,
  "expected_task_version": 2,
  "payload_sha256": "64hex",
  "status": "completed",
  "result": {},
  "error": null,
  "processed_at": "2026-08-11T00:00:01Z",
  "signature": "hmac-sha256-hex"
}
```

RPC 请求/响应：请求含 `correlation_id`、`kind`、`payload`、`reply_to`；响应含相同 `correlation_id` + `status` + `result`。事件消息：`type`、`task_id`、`run_id`、`global_seq`、`payload`、`occurred_at`。

### 5.3 可靠性

- Java 侧 `agent_command` 表保留为事务性 outbox；`@Scheduled` relay 用 `PESSIMISTIC_WRITE` 取 pending 行 → 发布（publisher confirm）→ 标记 published；失败按指数退避重试。状态机：`pending → published → accepted → completed/failed`。
- Python 侧 `internal_operations` 表幂等：同 operation_id + 同 payload_sha256 重放直接返回已存结果；同 id 不同 payload 返回 409；增加 `result_published_at` + 定时 sweeper 补发结果。
- 至少一次投递 + 双侧幂等，不追求恰好一次。
- 瞬时错误：nack(requeue=false) → DLQ → TTL 5s/30s/2min 重试，最多 5 次；校验失败/409 直接终态进 DLQ。
- 保序：commands 单消费者 + prefetch=1；多实例扩容时改为按 task 路由（二期）。

### 5.4 RPC 设计

- Python 使用独立 consumer/channel 处理 `rpc.requests`，与 commands 消费者分离，避免命令处理中等待 RPC 响应造成死锁。
- 请求/响应 10s 超时；超时后 Python 侧重试一次并记录事件；correlationId 过期清理。
- Artifact 传输：≤2MB 单条直传（base64），>2MB 分块（1MB/块，带序号与总块数）；Java 端按 `artifact_id` 校验所有权。

### 5.5 事件与 SSE

- Python 把运行时事件（26 种类型）持久化到运行时 MySQL，并向 `caijiatai.events` 发布；`text_delta` 类高频事件 250ms 聚合节流。
- Java 消费事件写入 `runtime_event` 投影表（含 `global_seq`），Web SSE 从 Java 读；Java 可用 RPC `list_events(after_seq)` 补偿重放。
- 事件不承载业务真值，仅承载运行时展示与观测。

### 5.6 心跳与健康

- Python 每 5s 发布 `heartbeat.ping`（消息 TTL 10s）；Java 消费后记录 last_seen。
- `/api/health`：`agent_status` 来自 last_seen（≤15s 为 up），Java readiness 不依赖 Python/RabbitMQ。

### 5.7 安全

- Java 与 Python 使用不同 RabbitMQ 用户（`java-svc` / `python-agent`）、统一 vhost `caijiatai`；凭据经环境变量注入。
- 消息带 HMAC-SHA256 签名（共享密钥 `AGENT_INTERNAL_HMAC_KEY`，≥32 字节）；支持轮换：配置 `active`/`previous` 双密钥，验签任一通过，轮换期双写新签。
- `payload_sha256` 校验内容完整性。
- Web→Java：仅 `127.0.0.1:8741`；生产模式 Host 校验 + 非安全方法 Origin 校验。
- 删除 `X-Agent-Internal-Token` HTTP 鉴权及其内部 HTTP 端点（见 §16）。

## 6. 存储与缓存

### 6.1 MySQL 8.0（单实例、双 schema）

- `caijiatai_business`：Java 业务表（Flyway 唯一建表）；金额 `DECIMAL`、JSON `JSON`、时间 `DATETIME(6)`。
- `caijiatai_runtime`：Python 运行时表（引擎状态、internal_operations、事件、RAG 事实表）。
- 旧 SQLite/PostgreSQL 数据不迁移，仅离线归档；历史 SQLite 迁移代码保留用于读取归档，不接线。

**schema 变更清单（实现时必须覆盖）**：

| 表/列 | 变更 |
|---|---|
| `agent_command.status` | 增加枚举值 `published`（原 pending/dispatching/accepted/completed/failed） |
| `agent_command` | 新增 `published_at DATETIME(6) NULL`（V4 迁移） |
| `internal_operations`（Python） | 新增 `result_published_at DATETIME(6) NULL` |
| `runtime_event`（Java，新表） | `global_seq`、`task_id`、`run_id`、`type`、`payload JSON`、`occurred_at`；`global_seq` 唯一索引 |
| `idempotency_record`（Java） | 保留现有结构，不变 |
| `rag_facts`（Python，二期） | 二期新增，本期不建 |

### 6.2 Redis（仅上下文缓存）

- Java：`ctx:task:{task_id}:v{generation}`，TTL 10min，generation 变更自然写新 key。
- Python：`ctx:run:{run_id}:e{epoch}` TTL 60s；`ctx:rag:{task_id}:{query_hash}` TTL 5min（二期）。
- 缓存层接口抽象，测试用内存 fake；Redis 不可用自动降级直查，不影响正确性。

### 6.3 Artifact

- 原件/快照/PO/邮件归 Java Artifact Store（SHA-256 两级分片、原子移动、locator 所有权校验）；模型消息/工具结果归 Python Runtime Artifact。

## 7. RAG 设计（二期）

- Python 侧：Milvus（开发 milvus-lite / 演示独立）+ 关键词混合 + rerank；embedding BGE-M3，rerank bge-reranker 或 Qwen。
- 关键词组件使用 `rank_bm25`（真 BM25）或如实标注“关键词检索”，**不使用“MySQL FULLTEXT = BM25”的说法**。
- Java 提供 RPC `list_history_facts`（按 decision_at/created_at 增量游标分页）；Python 可重入重建索引。
- 隔离约束：RAG 检索结果绝不进入 `input_sha256`/比价快照；检索指标在 MySQL/Milvus 上重新冻结并锁进测试。

## 8. 主线修复移植清单（选择性移植，不靠 merge）

| 来源提交 | 内容 | 落点 |
|---|---|---|
| `def5176` | 纸箱/卷材品类识别 | Java 品类字典 + Python parsing |
| `b12f770` | 物料/材质/颜色 fail-closed；运费到付/自付强制复核；PO CSV 公式注入防护 | Java comparison/quote + Web |
| `8f7ffa9` | 专票解析、卷材长度边界 | Python parsing + Java 校验 |
| `6f26605` | 规格“宽×长×高”方向；厚度公差上限 5000µm | Python capture + Java 校验 |
| `4a5bdf6` | .env 模型配置单一真源 | Python config |
| `d584159` | 修正后确定性重分析 | Java 快照失效 + Python 工具 |
| 其他 | RAG stage-6 代码（作为二期参考） | 二期 |

每项移植必须携带原提交的回归测试，并适配到新架构（Java/MySQL/MQ）。

## 9. 实施步骤（含里程碑与决策门）

- **Step 0 前置**：commit/stash `codex/fix-and-optimize` 未提交改动；启动 Docker Desktop；从 `4bcea7f` 建分支 `codex/java-python-mysql-rag`；验证基线（`mvnw.cmd test` 12/13 + `uv run pytest -q`）。
- **Step 1 Java 完整业务（4 成）**：PG→MySQL（V1–V3 重写、驱动、Testcontainers）；移植 §8 业务修复；黄金数据演示模式（用 `scripts/generate_procurement_scenarios.py` 冻结数据预置，Java seed 数据标记 `synthetic`，不混入生产审计）；Java 测试全绿。
  - **决策门 1**：`mvnw.cmd test` 全绿 + 黄金演示模式浏览器走通（无 Python）→ 才进 Step 2。
- **Step 2 嵌入 Python 微服务（6 成）**：
  - **2a 最小可行切片**：Python SQLite→MySQL（pymysql 连接池）后，先打通一条命令链路 Java→MQ→Python→MQ→Java 回传 + 事件回灌 Web SSE（用 `analyze` 命令）。
  - **决策门 2**：切片在浏览器可见状态流转（运行中→完成）且重启后能续跑 → 才铺开其余命令类型（start_conversation/import_quote/approve/reopen）。
  - 2b 全量命令/结果/RPC/事件/心跳 + Redis 上下文缓存；删除内部 HTTP 端点；双侧幂等 + DLQ + HMAC。
- **Step 3 RAG（二期，可选）**：Milvus + rank_bm25 + rerank；`list_history_facts` RPC；反馈闭环；冻结评测。
- **Step 4 收尾**：CI（Java/Python/Web + MySQL/RabbitMQ/Redis service）；Compose 五服务；文档同步（README/architecture/threat-model/release-checklist）；版本 0.5.0；冻结评测重新锁值；合流（新分支提升为新 master）。

## 10. 测试计划与验收门槛

**命令级验收**：
- Java：`mvnw.cmd test` 全绿（Testcontainers MySQL+RabbitMQ）：BigDecimal/税费/汇率、V1/V2 规格、黄金契约哈希、Artifact 防护、审批状态机、outbox 幂等/重放、stale approval、事务回滚/乐观锁、RPC 服务端、事件投影、心跳超时降级。
- Python：`uv run pytest -q`（MySQL 测试 schema + fake Redis + MQ 桩）：解析 617/620、Agent 行为回归、命令幂等/409、RPC 客户端超时重试、事件发布、Redis 降级、心跳。
- 冻结评测：`uv run python scripts/evaluate_procurement.py run` + `verify` 通过；**评测保持纯函数，不依赖运行时 DB**（`evaluate_frozen_cases` 直连 parsing + 真值 + 黄金文件）。
- Web：`npm test && npm run lint && npm run build` + `scripts/check_web_build_determinism.py`；浏览器走通全闭环。
- E2E：Compose 五服务 healthy；仅 8741 暴露；无 MQ 凭据访问失败；杀 agent 后命令滞留、重启续跑；杀 RabbitMQ 后 outbox 滞留、Java 降级可读。

**验收门槛（0.5.0 发布门禁）**：黄金契约哈希跨语言一致；Step 1/2 决策门均通过；全量测试绿；文档与实现一致。

## 11. 部署与 CI

- Compose：`mysql:8.0`（init 建两 schema）、`redis:7`、`rabbitmq:3-management`、`agent`（仅内网 8742）、`procurement`（宿主机 127.0.0.1:8741）。
- CI：Java job（setup-java 21 + `mvnw.cmd test`）；Python job（MySQL/RabbitMQ/Redis service container）；Web job（npm + 构建确定性）。

## 12. 环境前置与配置

- 前置：Java 21（已装）、Node 20+、Python 3.11 + uv、Docker Desktop（需启动，当前未启动）、本地 MySQL80（已运行）、无本地 Redis/RabbitMQ（走 Compose）。

| 环境变量 | 默认/示例 |
|---|---|
| `DATABASE_URL` | `jdbc:mysql://127.0.0.1:3306/caijiatai_business?useSSL=false&serverTimezone=UTC&characterEncoding=utf8mb4` |
| `DATABASE_USER` / `DATABASE_PASSWORD` | `caijiatai` / 本地或 Compose 注入 |
| `AGENTHARNESS_DATABASE_URL` | `mysql+pymysql://caijiatai:...@127.0.0.1:3306/caijiatai_runtime` |
| `RABBITMQ_URI` | `amqp://java-svc:...@127.0.0.1:5672/caijiatai` |
| `AGENT_RABBITMQ_URI` | `amqp://python-agent:...@127.0.0.1:5672/caijiatai` |
| `AGENT_INTERNAL_HMAC_KEY` | ≥32 字节随机串（Java/Python 共享） |
| `REDIS_URL` | `redis://127.0.0.1:6379/0` |
| `APP_PORT` | `8741` |
| `AGENT_PORT` | `8742` |
| `APP_ARTIFACT_ROOT` | `./data/artifacts` |
| `OPENAI_API_KEY/BASE_URL/MODEL` | 按 .env.example |
| `AGENTHARNESS_PROCUREMENT_PROVIDER` | `procurement_fake`（离线演示）或 `openai` |

## 13. 数据与迁移

- 业务与运行时数据**全新初始化**，不迁移旧 PostgreSQL/SQLite 数据（仅归档）。
- MySQL schema 由 Flyway（Java）与 Python 迁移脚本各自管理，禁止交叉建表。

## 14. 风险与缓解

| 风险 | 缓解 |
|---|---|
| Docker 未启动阻塞测试/Compose | Step 0 先启动并验证 |
| 未提交改动被误混入 | Step 0 先 commit/stash |
| MQ RPC 死锁/队头阻塞 | 独立 consumer/channel + 超时（§5.4） |
| 事件流丢失影响前端 | Python 持久化 + RPC 重放 + global_seq（§5.5） |
| Artifact 消息过大 | 分块传输 + `max_message_size=16MB`（§5.1/§5.4） |
| MySQL FULLTEXT≠BM25 被误写简历 | 用 rank_bm25 或如实描述（§7） |
| LangGraph 重写失控 | 明确不重写（§2） |
| 合并冲突 | 换主不合（§3） |
| MQ-only 集成不成立 | Step 2 先做 2a 最小切片，决策门 2 把关（§9） |
| 冻结评测被 DB 耦合 | 评测保持纯函数（§10） |

## 15. 变更管理

- 所有实现提交到 `codex/java-python-mysql-rag`；完成后提升为新 `master`，旧主线归档。
- 规格书随实现更新（v1.x），重大变更需更新本文件并重新评审。

## 16. 契约与接口变更清单

**删除（Java 内部 HTTP）**：
- `POST /internal/v1/commands`（Java→Python 命令派发）
- `GET /internal/v1/tasks/{id}/context`、`GET /internal/v1/artifacts/{id}/raw`、`POST /internal/v1/operations/{id}/result`
- `X-Agent-Internal-Token` 鉴权（HTTP 层）

**新增（MQ 消息/RPC）**：
- 命令/结果/RPC/事件/心跳消息类型与字段（§5.2）
- RPC kinds：`get_task_context`、`get_artifact`、`list_history_facts`（二期）、`list_events`

**变化**：
- `contracts/`：两个 HTTP OpenAPI 替换为 MQ 消息 JSON Schema + 黄金契约（保持 canonical JSON/SHA-256 不变）
- Web `/api/stream`：数据源从“Java 反向代理 Python”改为“Java 读 `runtime_event` 投影表”
- Web `/api/runs/**` 读接口：同样改为 Java 投影，不再代理 Python