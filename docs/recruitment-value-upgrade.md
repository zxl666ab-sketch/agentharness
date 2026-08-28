# 秋招含金量升级路线（Java + AI 双线）· v1.0

> 目标读者：本人（审核人）+ goal 模式执行 Agent（施工方）
> 时间窗口：2026-08 起 4~6 周（27 届秋招提前批已开始，正式批 9-10 月）
> 关联文档：`docs/resume.md`（简历）、`docs/platform-upgrade-design.md`（0.5.0 升级记录）、`README.md`
> 状态：待审核
> **范围决策（2026-08）**：用户要求只做项目完整度——简历/包装项（P0-0 简历双版本、P2 演示视频/GitHub 叙事）延后，不纳入本项目执行。执行顺序见 §6（已更新为纯项目版）。

---

## 0. 核心判断（先读这一节）

1. **项目底子已是学生项目前 5%**：微服务真实落地（Kafka 唯一通道 + outbox + HMAC + DLQ + SASL）、冻结评测（617/620、31/31）、受治理 Agent 运行时（Run/Lease/Checkpoint/工具重放策略/上下文预算/验证循环/审批闭环）——这三样普通学生项目一个都没有。
2. **最大浪费是"没变现"而不是"没功能"**：手里有完整 Agent 运行时，简历却只写"文档解析 + LLM 结构化"；Java 侧没有任何性能数字，面试官问"能扛多少并发"会露怯。
3. **策略**：
   - Java 侧补**量化性能证据**（压测调优闭环、虚拟线程、可观测性、MySQL 深度案例）——传统 Java 考点的最后一块拼图；
   - AI 侧打**前沿标签**（MCP、向量检索 + 引用溯源、LLM-as-Judge 评测），并把现有 Agent 治理讲成 **AgentOps / Governed Agent Runtime** 叙事；
   - 表达变现优先于功能堆砌：**第 1 周先改简历，再动代码**。
4. **时间盒**：P0（第 1-2 周）必须完成，P1（第 3-4 周）尽力，P2（第 5 周）可选。每个大项设硬超时，超时降级为"预研笔记 + 面试话术"，绝不拖延。

---

## 1. 现状体检

### 1.1 已超标的部分（守住，别动）

| 维度 | 证据 | 面试价值 |
|---|---|---|
| 微服务落地 | Java 业务主机 + Python Agent，Kafka 唯一通道（HMAC 签名、双侧幂等、DLQ、SASL/SCRAM 实测） | 远超学生项目 |
| 可靠消息 | `agent_command` outbox（本地消息表=事务消息）、命令状态机 pending→published→accepted→completed/failed、瞬时错误 4 次重投 | 超标，可讲"最终一致性" |
| 确定性评测 | 617/620（99.52%）、31/31、冻结黄金契约 Java/Python 双侧回归、评测资源冻结纪律 | 学生项目几乎绝版 |
| Agent 运行时治理 | RunEngine / RunLifecycle / LeaseManager / Checkpoint / ToolInvocationExecutor（重放策略、并行安全、审批范围）/ ContextPlanner（token 预算 + 压缩）/ VerificationLoop / EffectScheduler | **被严重低估，见 2.2** |
| 安全治理 | sandbox、redaction、allow-once 人工审批、SSRF/egress 防护、Prompt 注入对抗测试 | 超标 |
| Java 经典考点 | 注册式状态机引擎、三层并发防护、决定/订单事务一致性、分批收货幂等、发票核销门禁、双超时调度、BigDecimal、Flyway V1–V19 | 已齐 |
| 工程化 | GitHub Actions CI、Testcontainers 集成测试、契约测试、Playwright 无头验收、演示数据 synthetic 纪律 | 超标 |

### 1.2 含金量缺口（按面试被问概率排序）

**Java 侧：**

| # | 缺口 | 面试杀伤 |
|---|---|---|
| J1 | **无任何性能数字**（QPS/延迟/压测报告） | "系统能扛多少并发？"答不上 = 前功尽弃 |
| J2 | MySQL 深度无案例（EXPLAIN/慢查询/索引设计论证/事务隔离） | 必问 |
| J3 | Redis 只有锁 + 缓存，缓存三兄弟/双写一致性无落地证据（有 Noop 回退可讲） | 必问 |
| J4 | **Java 21 虚拟线程完全没用**（项目最大的版本卖点闲置） | 2026 必问 |
| J5 | 可观测性只有审计/心跳，无 metrics/tracing/告警 | 高频 |
| J6 | 无压测→调优→复测闭环，无 before/after 对比 | 与 J1 同题 |

**AI/Agent 侧：**

| # | 缺口 | 面试杀伤 |
|---|---|---|
| A1 | **Agent 含金量没变现**：简历写"解析 + 结构化"，实际持有完整受治理运行时 | 最大浪费，零成本修复 |
| A2 | RAG 已下线；若重启需先恢复确定性检索基础，再谈向量/混合/引用溯源 | 中（可选） |
| A3 | **无 MCP** | 2026 秋招 AI 应用岗必聊 |
| A4 | 评测只有字段抽取类，无 LLM-as-Judge / 解释质量评测 | 高频 |
| A5 | 无 token 成本/延迟/失败率看板（已有 429 退避素材） | 高频 |

---

## 2. 含金量基准对照

### 2.1 Java 后端高频考点对照

| 考点 | 本项目现状 | 差距 | 动作 |
|---|---|---|---|
| 并发控制 | 幂等表/乐观锁/Redis 分布式锁（SETNX+标识+Lua 释放） | 无 | 已超标，补充"为什么三层各司其职"话术 |
| 消息可靠性 | outbox + DLQ + 幂等消费 + HMAC | 无 | 已超标；可加"积压监控"指标（P0-2） |
| 状态机/设计模式 | 注册式通用状态机引擎 | 无 | 已超标 |
| JVM | 无调优案例 | 中 | 压测时观察 GC/内存，记录 1 个观察点（P0-1 附带） |
| 线程池/JUC | 无显式线程池/CompletableFuture/虚拟线程 | **高** | P0-3 虚拟线程实验 |
| MySQL | Flyway/索引齐全 | 无 EXPLAIN 案例、无深分页/锁机制论证 | P1-3 深度文档 + P0-1 真实优化 |
| Redis | 锁 + 看板缓存 + Noop 回退 | 缓存三兄弟/续期/一致性无落地 | P1-3 深度文档（可讲"锁过期由乐观锁兜底"的合理简化） |
| 分布式事务 | outbox=本地消息表（最终一致性） | 无 | 已有素材，讲"事务消息 + 幂等消费"叙事 |
| 微服务治理 | 双语言微服务、契约测试 | 无注册中心/网关 | **不做**，讲"为什么当前拓扑不需要" |
| 性能 | 无数字 | **最高** | P0-1 |
| 可观测性 | 审计/心跳/SSE | 无 metrics/tracing | P0-2 |
| 测试工程化 | CI/Testcontainers/契约/Playwright | 无 | 已超标 |

### 2.2 Agent 应用高频话题对照

| 话题 | 本项目现状 | 差距 | 动作 |
|---|---|---|---|
| 工具调用可靠性 | 参数校验、结果截断、重放策略（幂等/并行安全）、EffectScheduler | 无 | 已超标，写进简历 |
| Agent 生命周期 | Run/Checkpoint/Lease/恢复/预算 | 无 | 已超标，写进简历 |
| 上下文管理 | ContextPlanner token 预算、compaction 压缩 | 无 | 已超标，写进简历 |
| 安全治理 | sandbox/redaction/allow-once 审批/SSRF | 无 | 已超标 |
| 评测 | 冻结抽取评测 + 双侧回归 | 无 LLM-as-Judge | P0-4 |
| RAG | 已下线（历史报价 RAG 不再提供） | 无；重启需先恢复基础接线 | P1-2（可选重启） |
| MCP | 无 | **最高** | P1-1 |
| 多智能体 | 两阶段编排（start_conversation → analyze）已有雏形 | 无显式多 Agent | P2 叙事化即可，不写代码 |
| 成本/延迟 | 429 Retry-After + 有界退避 | 无成本看板 | P0-2 附带（LLM 成本指标） |
| AgentOps | 审计 + 评测 + 运行时 = 天然 AgentOps 素材 | 无叙事 | P0-0 简历变现 |

---

## 3. 升级路线（时间盒 4~6 周）

> 全局纪律：① 每个阶段结束跑全套测试 + 提交 commit；② 冻结资产（ApprovalService/ComparisonEngine/评测资源）一个字节不动；③ 新接口同步 contracts + 黄金契约；④ 演示数据 synthetic 纪律不变；⑤ **先提交当前工作区未提交的改动**（git status 现有 M 文件）。

### P0（第 1-2 周）——变现 + 性能证据，性价比最高

#### P0-0 简历/README 叙事升级（1 天，先做）
- `docs/resume.md` 出**双版本**：Java 后端版（突出 J1-J6 考点 + 性能数字占位）+ AI 应用版（突出 Agent 运行时治理 + 评测 + AgentOps 叙事）。
- 把 Python 侧描述从"文档解析与 LLM 结构化"升级为：
  > 自研**受治理 Agent 运行时**：Run 生命周期 / Checkpoint / Lease 租约 / 工具调用重放策略（幂等、并行安全、审批范围）/ 上下文 token 预算与压缩 / 验证循环 / 沙箱与脱敏 / allow-once 人工审批闭环。
- 给架构起名：**确定性引擎 + 受治理 Agent 双引擎**（"AI 负责理解，确定性规则负责正确性，治理负责安全"）。

#### P0-1 压测 + 调优闭环（4-5 天）
- 工具：k6（轻量、脚本化、可出 JSON 报告），README 说明。
- 四个必测接口（各有面试故事）：
  1. `POST /api/procurement/orders/{id}/transition` —— **分批收货幂等路径**：验证相同键重放不重复累计；
  2. `POST /api/procurement/requests/{id}/decision` —— **三层防护竞争路径**：并发审批只成功一个；
  3. `GET /api/procurement/insights/overview` —— 缓存命中 vs 穿透对比；
  4. `GET /api/procurement/requests` —— 列表分页。
- 产出指标：QPS、p50/p95/p99、错误率；对比组：缓存开/关、锁开/关（Noop）。
- 做 **2 个真实优化**（候选：insights 聚合索引、深分页 offset→keyset、履约聚合投影的 N+1），记录 before/after。
- 交付：`docs/performance-report.md` + `output/loadtest/` 数据 + README 链接。压测期间记录 1 个 GC/内存观察点（JVM 考点）。

#### P0-2 可观测性三件套（2 天）
- actuator 已有 → 加 `micrometer-registry-prometheus`；compose 加 Prometheus + Grafana（可选 Alertmanager 不装）。
- 自定义业务指标：命令 outbox 积压深度、DLQ 消息数、agent 心跳延迟、决策锁等待时长、insights 缓存命中率、LLM 成本/失败率（Python 侧经审计事件或心跳上报，Java 聚合）。
- 交付：监控面板截图（docs/evidence/）+ 指标清单文档。命中"可观测性/RED 指标"考点。

#### P0-3 虚拟线程实验（2 天）
- 场景：Kafka RPC 10s 超时等待是典型 IO 阻塞路径（AgentDispatcher/KafkaRpcServer）。
- 动作：`spring.threads.virtual.enabled=true` 或局部 `Executors.newVirtualThreadPerTaskExecutor()` 替换平台线程池；压测对比"固定线程池阻塞耗尽 vs 虚拟线程高并发"。
- 交付：对比数据 + 一句话话术："Java 21 虚拟线程解决 IO 密集下的线程池耗尽；本项目 RPC 等待是典型场景，实验数据 N 并发下平台线程池 X 线程阻塞 vs 虚拟线程无阻塞"。

#### P0-4 评测 CI 化 + LLM-as-Judge（2 天）
- 冻结评测接进 `.github/workflows/ci.yml`（Java/Python 双侧回归已存在，补评测步骤）。
- LLM-as-Judge：对"比价解释"质量评分——数字引用一致性（与快照自动比对，硬校验）、风险覆盖完整性、无幻觉；评测集放现有冻结资源之外的新扩展文件（冻结资源不动）。
- 交付：评测截图 + judge 评分表 + 与人工评分相关性说明。

### P1（第 3-4 周）——前沿标签

#### P1-1 MCP 化（5 天，**硬超时：2 天预研，跑不通降级为预研笔记**）
- 推荐路线：Java 侧暴露 **MCP Server**（Spring AI MCP Server 或手写 JSON-RPC 2.0），把领域能力标准化为 MCP tools（`query_supplier_profile`、`create_order`、`list_events` 等），任何 MCP 客户端（Claude Desktop / Cursor / 自研 Agent）可接入。
- 叙事："现网 Agent 走 Kafka 保吞吐与治理，MCP 是开放边界——领域能力一次实现，多端复用"；或反向：把 Python Agent 工具层按 MCP 协议暴露。
- 验收：一个外部 MCP 客户端实测调用 Java 工具成功 + 契约文档 + 截图。

#### P1-2 历史报价 RAG（已下线，暂缓）
- 现状：历史报价 RAG（K5）已随 814a90e 清理 Python 死代码整体下线；Java RPC、扩展评测与相关文档均已移除。
- 若重启：先恢复 Python `reference_prices.py` 接线与 Java `get_reference_prices` 服务，再评估向量/混合检索与引用溯源（原 P1-2 方案）。
- 面试点：如需重启，"什么时候确定性检索够用、什么时候必须上向量"仍可作深度题准备。

#### P1-3 深度文档（2 天）
- `docs/interview-deep-dive.md`：缓存三兄弟治理方案（穿透/击穿/雪崩）、分布式锁续期讨论（看门狗）与"锁过期由乐观锁兜底"的合理简化、Redis 双写一致性取舍、MySQL EXPLAIN 案例（P0-1 的真实优化）、事务消息（outbox）叙事。**文档即面试题库**，不一定要写代码。

### P2（第 5 周，可选）

- 演示视频/GIF（Playwright 录屏走全闭环，1 分钟）。
- GitHub 组织化：README 加性能报告/监控截图链接；star 无关，重点是**面试官 3 分钟看懂**。
- 多阶段编排叙事：把现有 two-phase（解析 → 分析）显式讲成"多阶段 Agent 流水线"，不写新代码。

### 明确不做（防范围蔓延）

- 登录/权限体系（本地单机工具，已有理由）；网关/Nacos/注册中心；K8s；换数据库/框架；动 Python 解析核心与冻结评测；为堆而堆的多 Agent、向量库选型大战（Milvus 一句话提及即可）。

---

## 4. 面试故事线（表达层）

- **一句话定位**："Java 业务主机 + 受治理 Python Agent + Kafka 可靠消息 + 冻结评测——确定性优先的双引擎采购平台。"
- **五个必答问题**（新增）：
  1. 你的系统能扛多少并发？→ P0-1 数字（QPS/p95）+ 分批收货/付款幂等实测；
  2. Java 21 给你带来了什么？→ 虚拟线程实验（P0-3）；
  3. 你怎么评估 LLM 输出质量？→ 冻结评测 + 引用一致性硬校验 + LLM-as-Judge（P0-4）；
  4. 你了解 MCP 吗？→ P1-1 实测；
  5. Agent 应用的安全边界怎么划？→ sandbox/redaction/allow-once 审批（已有）。
- **数字清单**：617/620（99.52%）、31/31、0 漏检、0 错误入选、测试数（Java/Python/Web 46+）、Playwright 45+ 项、压测 QPS/p95、评测集规模、监控指标数。

---

## 5. 风险与纪律

| 风险 | 等级 | 对策 |
|---|---|---|
| 秋招时间被功能吃掉 | 高 | 每项硬超时；超时降级为"预研笔记 + 话术"；P0-0 第一周先完成 |
| 压测破坏环境 | 中 | 只压本地 Compose；数据用 synthetic；不压生产数据卷 |
| MCP 跑不通 | 中 | 2 天预研门槛；降级后仍可面试讲协议设计 |
| 冻结评测被污染 | 高 | 新评测集独立文件；冻结资源一个字节不动 |
| 工作区未提交改动丢失 | 中 | 开工第一步：收尾提交当前 git status 改动 |
| 简历与 README 脱节 | 中 | P0-0 双版本简历与 README 同步，禁止单侧更新 |

---

## 6. 执行顺序摘要（纯项目版，写给执行 Agent）

0. **基线**：先向用户确认当前工作区未提交改动（git status 十余个 M 文件）如何处置（收尾提交 / 保持不动），再跑全套测试全绿作为开工基线
1. P0-1 压测 + 调优闭环（性能完整性）
2. P0-2 可观测性三件套（运维完整性）
3. P0-3 虚拟线程实验（Java 21 完整性）
4. P0-4 评测 CI 化 + LLM-as-Judge（AI 评测完整性）
5. P1-1 MCP 化（Agent 互操作完整性，硬超时 2 天预研）
6. P1-2 历史报价 RAG（已下线，暂缓；若重启先恢复基础接线）
7. P1-3 深度文档（考点完整性：缓存三兄弟/锁续期/EXPLAIN 案例/事务消息）
8. （可选）多阶段编排显式化——把现有 two-phase 流水线做成显式编排元数据，仅当 P1 有余量

**延后不做**：P0-0 简历双版本、P2 演示视频/GitHub 叙事（用户范围决策）。
每步验收：测试全绿 + commit + 证据落 docs/evidence/；README/架构文档随代码同步更新（属于项目完整性）。
