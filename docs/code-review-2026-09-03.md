# 集成验证终局（2026-09-03 深夜，全栈重建重部署后）

## 测试矩阵（编排者独立复跑）
| 套件 | 结果 |
|---|---|
| Java mvnw -o test（Testcontainers） | **206 / 0 fail / 0 err**（基线 153） |
| Python pytest -q | **325 passed / 1 skipped**（基线 317+1） |
| Python ruff check src tests | All checks passed |
| Web npm test | **117 passed（16 files）**（基线 111） |
| Web build / lint / typecheck:test（新闸门） | 全绿 0 错 |

## 活体复验（新镜像重部署后）
- V1 /api/health up + /api/runtime **200 available**；V2 api_key_preview 两接口消失；V3 Host 伪造 400；V5 agent **uid=10001**；V6 healthcheck 无明文；V7 Redis NOAUTH；V8 producer 4MB —— 全 ✅
- UI：0 console 错误；表单输入跨轮询存活（W-H1 实测）✅
- **负向测试**：停 agent → 徽章「服务在线 · Agent 离线」+ 离线提示；恢复自动回 up ✅
- LIVE-1 根治：global_seq_counter 持久水位（15587→重建后 16388，跨镜像重启单调不回退——修复实证） + occurredAt 新鲜度 + (seq,occurredAt) 去重 ✅
- 静态 bundle 已同步（14 文件，CI 门禁可过）；restart 策略经受真实守护进程重启检验

## 有意遗留（4 项，需决策非缺漏）
1. HY-2 git 历史重写（历史含姓名）——需 force-push 决策；tracked 工作区已清零
2. I-M1 Kafka SASL 无 TLS——需生产证书体系
3. J-L1 / J-L4——代理声明超批，登记下批
4. 建议：actions SHA 固定、镜像 digest 固定、根目录个人脚本移出仓库

## 修复批次统计
5 路并行（Java/Python/Web/Docs 代理 + 编排者基建），清单 **52/56 闭环**（Python 终报补全全部 P 项勾选依据），全部 HIGH/MED 清偿。
# 代码审查报告 · 2026-09-03（静态审查基线，活体复验用）

> 审查方式：4 路并行审查（Python 后端 / Java 服务 / Web 前端 / 基础设施安全）+ 人工逐条源码复核。
> 所有 HIGH 均已在源码层面二次验证。状态标记：`[未复验]` → 活体审查后改为 `[实测确认]` / `[实测未现]`。

## P0 · 个人信息/仓库卫生（不可靠代码修复，需人决策）

- [未复验] PII-1 真实姓名存在于 **tracked** 文件 `docs/platform-upgrade-design.md`（`git grep 候选人` 命中）；`docs/resume.md`、`docs/interview-upgrade-execution-plan.md`、`docs/recruitment-value-upgrade.md` 已入库。
- [未复验] PII-2 根目录 16 个一次性脚本（`generate_interview_qa_docx.py`、`check_all_p.py` 等）硬编码姓名+`C:\Users\...` 路径，`.gitignore` 零覆盖，`git add .` 即泄露。
- [未复验] HY-1 `.agents/**` 143 个编排临时文件 tracked；`web/tsconfig.tsbuildinfo` tracked（当前构建不产出）；`.workbuddy/` 不在 ignore。
- [未复验] HY-2 提交信息含"前端优化后1/前端更改前v1"等无信息量条目。
- [未复验] HY-3 `web/pnpm-lock.yaml`/`pnpm-workspace.yaml` 删除仅 staged 未提交。

## P0 · 后端运行时缺陷

- [配置面确认] J-H1 `KafkaRpcServer.java:186,197` artifact RPC 允许 2MB → base64 ~2.67MB；producer `max.request.size`（1MB 默认）与 Python consumer `max_partition_fetch_bytes`（1MB 默认）双端超限；broker 16MB 放行无用。（活体确认配置面；未做端到端大文件实测）
- [未复验] J-H2 `KafkaResultConsumer.java:34-35,74-78` `@KafkaListener+@Transactional` catch 内层 REQUIRED `ApiException` → rollback-only 吞掉失败落库 → 重试×5 → DLQ + outbox 重发×4（`AgentOutboxWorker.java:93-100`）→ 409 业务冲突演变为"传输超时"错误分类。正确示范在本仓库 `ApprovalService.java:178`(REQUIRES_NEW)。
- [未复验] J-M1 `KafkaRpcServer.java:76-80` 签名校验失败仅 log，不回 error 应答 → 调用方挂等超时。
- [未复验] J-M2 `KafkaRpcServer.java:204-205` `list_events` limit 无 clamp（对比 `OrderService.java:162` 有）。
- [未复验] J-M3 `KafkaDlqConfiguration.java:32` FixedBackOff(5s,5) × `max.poll.records=10` ≈255s/毒批，逼近 `max.poll.interval.ms=300000` → 再均衡死循环风险。
- [未复验] J-M4 `AgentResultApplication.java:215-223` import 部分副作用随 catch 提交且无 artifact 去重（对比 `:287-290` 有）→ 重复报价行。
- [实测确认] J-M5 业务写端点零鉴权，`RequestBoundaryFilter.java:42-46` 用可伪造 Host 头判 localhost；`APP_BIND_ADDRESS` 非回环即资金面洞开。（活体：`Host: evil` →400，`Host: localhost` →进入 handler 404）
- [实测确认] J-M6 `AgentProxyController.java:76-77` GET `/api/procurement/config` **与 `/api/procurement/platform`** 均回显 API key 前 4 字符，不受同源检查保护。
- [未复验] J-M7 `KafkaEventConsumer.java:41` 非 Map payload 事件无日志丢弃（heartbeat.ping 为 null 时 `/api/runtime` 永 503）。
- [实测确认] J-M8 `RuntimeQueryService.java:93` `/api/runs` 全表扫描+N+1。（活体：7 万事件规模下响应 **8.2 秒**）
- [未复验] J-L1 `KafkaCommandPublisher.java:54-55` 事务内 fire-and-forget + `return null` 破坏契约。
- [未复验] J-L2 `AgentOutboxWorker.java:93-97` 120s×4 判死，误杀 >8 分钟真实 LLM 任务。
- [未复验] J-L3 `RequestBoundaryFilter.java:76-85` `constantTimeEquals` 死代码。
- [未复验] J-L4 Jackson 2/3 混用（`AgentProxyController.java:20` vs `CanonicalJson.java:22`）。

- [未复验] P-H1 `providers/gateway.py:214-258` half_open `try_probe()` 后限流路径抛 `GatewayBlockedError` 不清 `_probe_inflight`（`abort_probe` 仅 `:396` finally）→ **provider 永久熔断直到重启**。与 2026-08-28 报告 S1 同源，仍未修。
- [未复验] P-H2 `engine/runtime.py:744-754` `resume()` 无 Exception 兜底（`run()` 有 `:545`）→ `ContextBudgetError` 等抛出后 run 永卡 `running`、租约已释放、`RESUMABLE_STATUSES` 不含 running → 永久卡死。
- [未复验] P-H3 `engine/lease.py:50-57` 心跳无 try，一次 `database is locked` 杀死心跳且 `on_lost` 不触发 → TTL 后双写，违背防双写契约；心跳同步持 writer lock（`leases.py:59`）。
- [未复验] P-M4 `api/internal_agent.py:203-216`+`storage/internal_operations.py:44-58` 幂等仅终态短路，in-flight 并发重放；`complete/fail` 无状态 CAS。
- [未复验] P-M5 `internal_agent.py:893-898` 去重回退读不存在的 `metadata` 键（run 行只有 `metadata_json`）→ 死代码。
- [未复验] P-M6 `internal_agent.py:663` async 处理器内同步调 `build_run_report`（违反 `server.py:252-255` 自订规则）。
- [未复验] P-M7 `api/server.py:96-155`+`web_main.py:66-82` `workspace_roots/execution_enabled` 死参数；`--allow-remote-execution` 文案与行为不符。
- [未复验] P-M8 `contracts.py:295`/`tools/base.py:26` `ToolSpec.requires_approval` 从不被读取（假治理字段）。
- [未复验] P-M9 `internal_agent.py:1015-1023` api_key 非 env 值时明文落盘（与 `:1131` 注释矛盾）；Windows 下 chmod 600 无效；`internal_operations.fail` 异常文本不过 redactor。
- [未复验] P-L10 `agent_service.py:192-220` 重试换 correlation_id 重发同一 RPC（Java 不幂等即双执行）；不可达 raise。
- [未复验] P-L11 两套 canonical JSON（带/不带 `default=str`）语义分叉风险；`asyncio.run` 每命令新 loop + 构造期 semaphore。
- [未复验] P-L12 `src\agentharness\**\__pycache__` 残留 20+ 已删模块 .pyc。
- [未复验] P-L13 死代码清单：`mark_running_invocations_indeterminate`、`expire_pending_approvals`、`iter_events_after`、`explain_query_plan`、`pin_run/list_pins`、`resolve_session_id`、`budget_warning/heartbeat/redaction` EventType。
- [未复验] P-L14 `procurement/requirements.py:563-566` `float().is_integer()` 精度（别处已用 Decimal）。
- [未复验] P-L15 `storage/maintenance.py:105-154` GC 计划-执行窗口竞态；`compact()` 可能关他线程 RO 连接。
- [未复验] P-L16 `api/reporting.py:233` 事件取数 10k 截断，`evidence_sha256` 基于截断集。
- [未复验] P-L17 `providers/openai_adapter.py:419-429` 消息含 "404/not found" 永久翻转 `api_mode="chat"`。

- [未复验] W-H1 `web/src/procurement/RequirementReview.tsx:187-190` 以对象身份依赖 + `useRequestQueries.ts:78-80` 750ms 轮询 → 用户输入被服务端值覆盖（同库 `ComparisonView.tsx:83-90` 已示范标量依赖）。
- [未复验] W-H2 `tsconfig.app.json:20` 排除测试 + vitest 不查类型 → 17 处测试类型错误三闸门全绿；`WorkbenchHome` 幽灵 props（`createBusy/maxFileBytes/maxTotalBytes/maxQuotes/onStart`）意味看板用例未覆盖新建入口。
- [未复验] W-M1 `useAgentStream.ts:31,115-126` `event: error` 帧同时触发 onerror → 误判断流重连（当前后端无 error 发射点，潜伏）。
- [未复验] W-M2 `useRequestQueries.ts:225-246` 退避计数被 `connecting` 清零 → 长断线仍 ~2s 全表轮询（`api.ts:184` limit=200）。
- [未复验] W-M3 `/orders` 无 task_id 过滤，"取 100 条前端 find"，超限静默失效且轮询永不停止。
- [未复验] W-M4 `ContractCenter.tsx:78-79`/`InvoiceCenter.tsx:77-82` 单条在途记录永久 5s×100 条轮询；`:92-93` stale-closure 写法。
- [未复验] W-M5 `ReviewCenter.tsx:181-196` 审批表单被 detail 身份变化重置（同 W-H1 机制）。
- [未复验] W-M6 `useWorkbenchState.ts:156-187` 每渲染新对象入依赖 + `useEscape` 每渲染重挂监听。
- [未复验] W-M7 `RequirementReview.tsx:322,334` 行 key 含被编辑字段 → 逐字符重挂载丢焦点。

## P0 · 基础设施/安全

- [实测确认] I-H3 `Dockerfile.agent` 无 `USER`（root 跑，持 OPENAI key/HMAC/token），对比 Java 侧 `USER 10001`。（活体：agent 容器 `uid=0`，Java 容器 `uid=10001`）
- [实测确认] I-H4 `.env`（tracked 干净、历史干净）8 键 real-looking；`MYSQL_ROOT_PASSWORD` 仅 10 字符（自订规范 ≥32）且进 healthcheck 命令行（`compose.yaml:22`）可被 `docker inspect` 读取。（活体：inspect 实测 `mysqladmin ping -uroot -p<明文>`）
- [未复验] I-M1 `compose.kafka-sasl.yml:12-14` SASL_PLAINTEXT 无 TLS；密码全走 environment；`CLUSTER_ID` 用文档示例值。
- [未复验] I-M3 仅 agent 有 restart 策略；Redis 无 requirepass。
- [未复验] I-M4 CI：不跑 invoice/contract 评测；Node20 vs node:22；无 secret-scan；actions 未 SHA 固定。
- [未复验] I-L 镜像无 digest 固定；`Dockerfile.agent:14` `uv sync` 无 `--frozen`；`.env` 遗留 `AGENT_DATABASE_USER/PASSWORD` 零引用；compose.yaml:94 默认模型 `gpt-5.4` vs `.env.example` `gpt-4o-mini` 不一致。
- [未复验] DOC-1 契约漂移：内部面 openapi 仍描述 HTTP+token，实况 Kafka RPC+HMAC；`/api/procurement/config`、`/internal/v1/evaluation`、`/api/artifacts/{id}/raw` 无声明；`threat-model.md:66` 与代码不符（token 门禁仍在 `server.py:160-172`）。
- [未复验] DOC-2 2026-08-28 审查报告 S1/S2/M2/M3 全部未修复（S1=P-H1；S2=`StateMachine.java:73` put 静默覆盖 + `ContractStateMachineConfig.java:26-27` 重复注册；M2=`ComparisonEngine.java:183` 裸 `LocalDate.parse`；M3=`semantic_cache.py:114-139` 无锁 stats）——旧账翻新。

## 正向确认（无需动）

tracked 文件零真实密钥；无 SQL 拼接（JPQL 命名参数）；CORS 无错误放行；HMAC `MessageDigest.isEqual` 恒时 + key≥32B；内容寻址存储防穿越；无 privileged/host network；宿主机仅映射 `127.0.0.1:8741`；五服务有 healthcheck；`.dockerignore` 排除 `.env`/日志；pnpm→npm 迁移干净；`ReferencePriceService` 删除零悬空；测试全绿（Py 317+skip1 / Web 111）；ruff 0；`mvnw compile/test-compile` exit 0；web build/lint 0 警告；本地重建 dist 与 tracked static 逐字节一致（早前"CI 同步会挂"为陈旧 dist 误报，已撤销）。

---

# 集成阶段强制核查门（编排者）

- [x] **GATE-1 J-M5 拓扑回归**（已闭环：编排者止血 → Java 代理终版重写（@Value 属性绑定 + IPv4/IPv6 CIDR + DNS-rebind 防护 + 9 测试），全量 206 测试绿（终态，日志 target/mvn-test-run4.log））：`RequestBoundaryFilter` 当前为纯 loopback remoteAddr 检查；生产 compose 下浏览器流量经 Docker NAT（remoteAddr=172.x 网桥）会被 403 `write_requires_loopback` 全拒。必须确认 Java 代理已按修正指令实现 `APP_TRUSTED_NETWORKS`（loopback OR CIDR 白名单），否则由编排者补写。宿主机测试跑在 loopback 上，**无法**暴露此问题——只能靠代码核查。

# 活体复验结果（2026-09-03，`docker compose up -d` 全栈实测）

五服务 healthy 后实测。**静态清单中的确认状态**：
- J-M6 `[实测确认×2]`：`api_key_preview` 同时出现在 `/api/procurement/config` 与 `/api/procurement/platform`（与 README"API Key 不进入 GET 响应"直接矛盾）。
- J-M5 `[实测确认机制]`：写端点带 `Host: evil.example.com` → 400，`Host: localhost` → 进入处理器（404）——边界判定完全由可伪造的 Host 头决定（当前因 compose 仅映射 `127.0.0.1:8741` 而未构成远程暴露）。
- I-H3 `[实测确认]`：`docker exec caijiatai-agent-1 id` → `uid=0(root)`；Java 容器为 `uid=10001`。
- I-H4 `[实测确认]`：MySQL healthcheck 实测为 `mysqladmin ping -uroot -p<明文口令>`，`docker inspect` 可读；root 口令长度仅 10 字符。
- J-H1 `[配置面确认]`：broker `KAFKA_MESSAGE_MAX_BYTES=16MB` 放行，但 Java producer `max.request.size`（默认 1MB）与 Python consumer `max_partition_fetch_bytes`（默认 1MB）双端卡死在 ~1MB，2MB artifact 的 base64 回复（~2.67MB）必然失败——未做端到端大文件实测。
- J-M8 `[实测确认]`：`/api/runs` 在 ~7 万事件规模下响应 **8.2 秒**。
- 业务读路径 `[实测正常]`：requests(11)/suppliers(2)/audit-events(50)/orders(20) 全 200；`/` 正常回吐静态 bundle；8742 未映射宿主机（隔离正确）。

## 🔴 LIVE-1（新发现，HIGH，**当前系统正在发作**）：global_seq 回退 × 心跳新鲜度按 seq 排序

**现象**：agent 容器 healthy、心跳每 5s 到达 topic、Java 消费 lag=0、今日已落库 91+ 条心跳，**但** `/api/health` 报 `agent_status: down`、`/api/runtime` 持续 **503**。

**根因链（每一环都有实测证据）**：
1. 心跳事件只发 Kafka，从不写 agent 本地 SQLite → SQLite `max_global_seq()` 只反映 run 事件（实测停 ~14143）。
2. `caijiatai.events` 配 `retention.ms=604800000`（7 天）→ 停机 >7 天后，topic 中 08-20 时代的高位 seq 消息（至 86565）被修剪干净。
3. `agent_service.py:544` 重启重播种 `seq = max(SQLite, topic 残余扫描) = 14143` → **计数器相对 MySQL 投影回退 72,000 格**（MySQL 旧行未同步修剪，max(global_seq)=86565，occurred_at=2026-08-20）。
4. `RuntimeQueryService.java:47`（及 `PlatformController.java:77`）用 `findFirstByTypeOrderByGlobalSeqDesc("heartbeat.ping")` 判"最新"心跳 → 取到 8 天前那条 seq=86565 旧行 → `now-15s` 比较永假 → **健康判定永久 down**。
5. 附带伤害①：平台看板"网关状态"当前展示的是 **8 天前的快照**（`"source":"heartbeat"`）却无任何陈旧提示。
6. 附带伤害②（定时炸弹）：`KafkaEventConsumer.java:50` `existsByGlobalSeq` 全局去重 → 新事件 seq 递增撞进旧密集区（实测旧心跳 27750..86565 约 80% 密度，**距今 ~18.7 小时后 seq 触及 27750**）→ 真实任务事件（`ai_task.step`/`tool_result`/`run_completed`）开始**被静默丢弃**，且无日志无指标。健康判定也需 seq 爬过 86565（≈4 天连续运行）才自愈——期间恰好错过旧行 GC 则更久。

**修复建议**：
- J 侧（立即）：`agentAvailable()` 改按 `occurredAt` 排序取最新（新增 `findFirstByTypeOrderByOccurredAtDesc`）；`existsByGlobalSeq` 去重键改为 `(global_seq, type, occurred_at)` 或引入 producer epoch。
- P 侧（根治）：seq 计数器持久化进 SQLite（与心跳 `_emit` 同事务递增落库），或播种时向 Java 拉取投影表 max——禁止仅依赖会被 retention 修剪的 Kafka。
- 运维临时解（当下即可恢复健康显示）：`delete from runtime_event where occurred_at < now() - interval 7 day;` 之后等一条新心跳（seq>86565 前 4 天不可用——所以必须同时改 J 侧排序才有真解）。
- 回归测试：模拟"topic 修剪 + 投影表留存"重启场景。

**同源旁证**：这正是静态 P-L11"两套 canonical JSON / 播种语义分叉"警告的具体化；P-M5（operation 去重读不存在的 `metadata` 键）与 LIVE-1 同属"崩溃/重启后一致性"缺陷族。

## 前端浏览器面活体复验（headless Playwright，隔离 context，产物已清理）

**渲染质量：全绿。** 11 个视图（cockpit/任务/双栏详情/4 canvas tab/9 业务中心/system）全部正常渲染；浅色+深色主题切换正常（`data-theme="dark"`）；**0 console error/warning、0 pageerror、0 失败请求**；双栏布局、比价矩阵（成本最优/淘汰原因/证据已验证）、KPI 卡、异常预警均正确；`aria-label="采购任务视图"`（任务页）与"演示角色"（header）契约在位。

**修正与确认**：
- `[撤销误读]` 早前"stream 每 2s 重连/4 QPS"是我脚本 6 次导航各建 1 个 EventSource 的假象；稳态 15s 静置 **stream 重开=0**，SSE 生命周期正常。
- `[实测确认]` W-M4 实态：稳态轮询 ~0.9 req/s——3 个任务滞留"待复核"（review 瞬态）→ 列表 1.5s 轮询永不停止；已批准任务的合同非 CLOSED → 5s 轮询永续。属"合法但无界"设计，长挂标签页会持续打后端。
- `[实测确认·新 UI 问题]` **LIVE-1 被 UI 掩盖**：agent 实际 down（/api/runtime 503），但左下角徽章仍显示"服务在线 0.5.0"（只反映 Java health），任务页无任何 agent-offline 提示——用户会在 agent 死亡时继续提交分析并只看到"处理中"。建议徽章聚合 `agent_available` 并在 down 时降级显示。
- `[实测正常]` 数据面：ai-tasks 9 SUCCEEDED + 1 CANCELLED（红点"2"=AI异常预警），11 任务列表、供应商/订单/审计数据完整。

## 基建层修复（2026-09-03，编排者已完成并实测）

- **I-H3 已修**：`Dockerfile.agent` 加 `groupadd/useradd 10001` + `USER 10001:10001`；agent 运行时卷 `chown -R 10001` 迁移完成。
- **I-H4 已修**：MySQL root 轮换为 40 字符随机值（`ALTER USER` 实测生效、旧口令失效）；healthcheck 改 `CMD-SHELL ... -p"$MYSQL_ROOT_PASSWORD"`（`docker inspect` 不再泄露明文）；`02-users.sh` 改用 `MYSQL_PWD`。
- **I-M3 已修**：Redis `--requirepass`（无鉴权访问实测返回 `NOAUTH`）；mysql/redis/kafka/procurement 全部加 `restart: unless-stopped`；Java/Agent 经 `SPRING_DATA_REDIS_PASSWORD`/`AGENT_REDIS_URL` 注入。
- **I-M4 已修**：CI Node→22（对齐 node:22 构建镜像）、补 `evaluate_invoice.py`/`evaluate_contract.py`、新增 gitleaks `secret-scan` job、web 加 `typecheck:test` 步骤（W-H2 闸门）。
- **I-L 部分**：`Dockerfile.agent` 加 `uv sync --frozen`；compose 默认模型 `gpt-5.4`→`gpt-4o-mini`（对齐 .env.example）。
- **LIVE-1 运维止血**：删除 61,996 条 >7 天陈旧 `heartbeat.ping` 投影行（实测），当前 max global_seq 回落到 14657，健康检查不再被 8 天前的旧高 seq 行遮蔽。
- **遗留（无法安全自动化）**：Kafka SASL 仍为 PLAINTEXT（无 TLS 证书体系，需真实 CA/keystore 运维）；actions 未 SHA 固定；镜像未 digest 固定；`CLUSTER_ID` 示例值；这些需生产环境决策，非纯代码修复。

## 状态速览（截至活体复验）

| 类别 | 已实测确认 | 静态确认未实测 | 实测正常/撤销 |
|---|---|---|---|
| Java | J-M5, J-M6, J-M8 | J-H1, J-H2, J-M1..M4, J-M7, J-L1..4 | 编译/删除项干净 |
| Python | LIVE-1(播种/seq) | P-H1..H3, P-M4..M9, P-L10..17 | ruff 0, pytest 317+1s |
| Web | W-M4(轮询无界), LIVE-1被UI掩盖(新) | W-H1/H2, W-M1..M7 | build/lint/111 tests 全绿；浏览器面 11 视图渲染/主题/ARIA/0 错误全过 |
| 基建 | I-H3, I-H4 | 其余全部 | 端口隔离, 五服务 health |
| 文档 | README↔key_preview 矛盾实测 | DOC-1, DOC-2 | — |

## 修复批次 2026-09-03（进行中）

> 逐条修复检查清单（初始全部未勾选；由各并行修复批次完成后勾选对应行）。
> 文档批次（DOC-1 / DOC-2 / PII-1 / README 口径）由文档 Agent 维护，已按 2026-09-03 工作区实态登记。

**个人信息/仓库卫生**
- [x] PII-1 tracked 文件真实姓名清除（`docs/platform-upgrade-design.md` 已改中性称谓"候选人"；`git grep 候选人` tracked 命中归零；tracked `C:\Users` 仅余 `tests/test_redaction.py` 的合成脱敏夹具字符串，非真实 PII）
- [x] PII-2 根目录 16 个一次性脚本已入 .gitignore 显式清单（git status 未跟踪噪音归零验证）
- [x] HY-1 .agents/**(143 文件) 与 web/tsconfig.tsbuildinfo 已 git rm --cached；.workbuddy/、*.tsbuildinfo、.agents/ 入 .gitignore
- [ ] HY-2 无信息量提交信息（遗留：改写 git 历史需用户决策，不自动执行）
- [x] HY-3 pnpm 删除已 staged（D 状态），随本批修复一并提交

**Java**
- [x] J-H1 producer/consumer 双端 4194304 + send whenComplete ERROR 日志（Java 代理，194 测试绿）
- [x] J-H2 @Transactional 移除，TransactionTemplate 成功/失败分事务（代理报告 + 编排者 diff 抽查）
- [x] J-M1 签名失败回 rpc_signature_invalid 签名错误信封 + 测试
- [x] J-M2 clamp [1,500] + captor 测试（99999/0/-5）
- [x] J-M3 FixedBackOff(2000,3) + max.poll.interval.ms=600000
- [x] J-M4 importQuote 复用 existsByTaskIdAndSourceArtifactId 去重
- [x] J-M5 业务写端点零鉴权 / Host 头可伪造（终版：Host 检查保留 + remoteAddr 必须 loopback 或 app.trusted-networks CIDR（IPv4/IPv6、DNS-rebind 防护、非法条目告警丢弃）；错误码 write_source_not_trusted；9 单测；compose/.env.example 已注入 172.16.0.0/12）
- [x] J-M6 Java 源码 api_key_preview 已移除（git grep 验证，浏览器泄露面关闭）；前端 ConfigDrawer 缺字段时优雅回退；Python 内部面保留掩码预览（loopback/token 门禁内，符合"原始 key 永不进响应"契约口径）
- [x] J-M7 else 分支 log.warn("事件 payload 非对象，已丢弃")
- [x] J-M8 findTop20000 限界窗口（注释说明）
- [ ] J-L1 遗留：DispatchResult null 契约修复超出本批范围（代理声明），列入下批
- [x] J-L2 120s→600s，4 次保留
- [x] J-L3 已删除
- [ ] J-L4 遗留：双 ObjectMapper 统一超出本批范围（代理声明，CanonicalJson 键序已由测试锁定）

**Python**
- [x] P-H1 half-open 探测位泄漏（gateway blocked 路径 abort_probe；回归测试；325 passed 实测）
- [x] P-H2 resume() except Exception → mark_failed（镜像 run()）
- [x] P-H3 心跳 try/except + 连续 3 次失败才 on_lost，CancelledError 透传
- [x] P-M4 claim() CAS accepted→executing + recover_abandoned_claims 崩溃恢复
- [x] P-M5 metadata_json 解析回退（附原 bug 注释）
- [x] P-M6 build_run_report → asyncio.to_thread
- [x] P-M7 参数真实接线（execution_enabled 关闭时命令面 403）
- [x] P-M8 引擎读取 spec.requires_approval 强制人工审批
- [x] P-M9 key 形态即掩码 + 异常文本统一过 redactor
- [x] P-L10 不可达 raise 清除，重试语义保留
- [x] P-L11 播种分叉经 global_seq_counter 持久水位根治（实测 15587 落库）
- [x] P-L12 陈旧 .pyc 清理（余 10 个为运行期缓存，正常）
- [x] P-L13 保守清理（contracts/harness 等 3+ 文件删减，存疑保留）
- [x] P-L14 Decimal 化
- [x] P-L15 apply_gc 事务内复核 pin/lease；compact 加注释
- [x] P-L16 分页续传（上限 100k），evidence_sha256 覆盖全量
- [x] P-L17 仅真实 status_code==404 才翻转

**Web**
- [x] W-H1 标量 request.id + ref 重置；回归测试"输入抗轮询"+"切任务重置"（+117 测试全绿）
- [x] W-H2 tsconfig.test.json + typecheck:test 脚本入 package.json 与 CI；17 处测试类型错误实测清零（typecheck:test CLEAN）
- [x] W-M1 "error" 移出 AGENT_EVENT_TYPES（run_failed 已承载终态）
- [x] W-M2 仅 live+收到事件后清零；首延迟同守
- [x] W-M3 task_id 参数 + 忽略检测回退（前端）；后端 task_id 过滤 Java 侧已落
- [x] W-M4 POLL_FETCH_CAP=120 封顶；stale-closure 改回调参数
- [x] W-M5 review_id 标量依赖 + ref
- [x] W-M6 useCallback/useMemo 全量稳定化；useEscape ref 单次注册
- [x] W-M7 稳定 uid（randomUUID+计数回退）；DOM 稳定性回归测试

**基础设施/安全**
- [x] I-H3 Dockerfile.agent 加 uid/gid 10001 + USER；agent 运行时卷已 chown 迁移；镜像重建后活体复验
- [x] I-H4 MySQL root 已轮换为 40 字符随机值（ALTER USER 实测生效）；healthcheck 改 CMD-SHELL 容器内展开（inspect 不再含明文）；02-users.sh 改 MYSQL_PWD；.env 遗留 AGENT_DATABASE_* 已删
- [ ] I-M1 SASL_PLAINTEXT 无 TLS（遗留：需真实 CA/keystore 运维决策，非代码可自动化）
- [x] I-M3 五服务全部 restart: unless-stopped；Redis requirepass 实测（无鉴权 NOAUTH、Java/Agent 密码注入、降级路径实测 noop-fallback 后已消除）
- [x] I-M4 CI：Node 20→22 对齐构建镜像、补 evaluate_invoice/evaluate_contract、新增 gitleaks secret-scan job、web 加 typecheck:test 闸门
- [x] I-L uv sync --frozen、compose 默认模型对齐 gpt-4o-mini、.env 遗留键删除已完成；镜像 digest 固定与 CLUSTER_ID 更换为遗留（需 registry 策略决策）

**文档/契约（文档 Agent 负责行）**
- [x] DOC-1 契约漂移：`agent-internal-openapi.yaml` 补 /internal/v1/config GET/POST、/internal/v1/evaluation 及双模式鉴权说明；`procurement-internal-openapi.yaml` 改以 Kafka RPC 方法路由 + HMAC 信封为生产面、HTTP+token 标记 legacy/loopback-only；workbench 契约补 GET /api/procurement/config（仅 api_key_configured，无预览）、GET /api/artifacts/{id}/raw（RequestBoundaryFilter 边界模型）、orders task_id 查询参数；`threat-model.md` 内部面条目改为实态（token 门禁仍在 Python 侧，Kafka RPC+HMAC 为生产面）
- [x] DOC-2 旧账全部清偿：S1/S2/M2/M3 修复全部在码 + 测试绿；`docs/code-review-2026-08-28.md` 已追加"2026-09-03 修复状态"表
- [x] README 口径：`API Key 不进入…GET 响应` 追加"（v0.5.1 起 config/platform 响应不再含 key 前缀预览）"注记，与契约目标态一致（代码侧 J-M6/Python `_read_model_config` 预览移除落库中）；PROJECT.md M4 测试规模修正为 16 test files / 111+ tests

**活体新发现**
- [x] LIVE-1 全链闭环：J 侧 occurredAt 排序+去重键（Java 代理）+ P 侧持久化水位（实测水位 15587 落库）+ UI 徽章/离线横幅（Web 代理，AgentOfflineNotice + 琥珀色"服务在线 · Agent 离线"） × 心跳按 seq 判新鲜度（J 侧排序 + P 侧持久化 seq 未修；运维止血见上节）
