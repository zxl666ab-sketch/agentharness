# 代码审核报告 — 采价台 P1→P3 升级面（2026-08-16）

> 审核对象：commits `c8be795`(P1)…`795e5dd`(最终报告)，基线 `df0a173`。仓库根 `D:\个人通用agentharness`。
> 方法：6 个只读审核代理并行分域（Java 后端 / Python Agent / Web 前端 / 契约与评测 / 全仓纪律与安全 / Java↔web↔契约↔Python 交叉一致性），随后对全部 high 与关键 medium 手工读码复核（`◐`=已复核确认；`○`=双代理独立一致报告，未逐一重读；`·`=单代理报告）。
> 结论：**无 critical 级问题**（无数据损坏、无密钥泄漏、冻结资产零改动、测试全绿状态未被破坏）；**3 条 high 已确认**（1 条为真实业务逻辑 bug，2 条为故障路径/健壮性缺陷）；若干 medium（多为一致性/健壮性/文档与产物不一致）。纪律与确定性设计整体优秀。

---

## 一、高危发现（3 条，均已读码复核确认 ◐）

### H1. 合同「变更驳回」把生效合同错误重置为 DRAFT — 真实逻辑 bug
- 位置：`ContractStateMachineConfig.java:26`（`CHANGE_REQUEST --REJECT--> EFFECTIVE`）vs `Contract.java:146-152`（`reject()` 无条件写 `DRAFT.wireValue()`）vs `ContractService.java:251-260`（reject 分支调 `contract.reject(body.notes())`）。
- 触发：EXECUTING 合同发起 `request_change` → CHANGE_REQUEST → 人工驳回。状态机声明回 EFFECTIVE，但实体被写成 DRAFT，丢失执行态，需重新 submit→approve 才能恢复；PENDING_APPROVAL 来源的驳回回 DRAFT 是正确行为。
- 修法：`Contract.reject()` 改为按来源分流——`from==CHANGE_REQUEST` 时恢复到发起变更前的 EFFECTIVE/EXECUTING；或定义 `undoChange()` 专用于变更驳回；保证状态机构造、实体、web 提示三者一致，并补状态机测试（变更驳回→EFFECTIVE）。

### H2. LLM 网关限流/熔断异常永远不重试（与 P2-1 意图相悖）
- 位置：`src/agentharness/engine/runtime.py:66-68`（`_RETRYABLE_PROVIDER_ERRORS = {rate_limit, timeout, connection, server_error}`）与 `:76-78`（`code in {rate_limited, circuit_open}` 返回该复数键名）与 `:1296`（`retryable = error_kind in _RETRYABLE_PROVIDER_ERRORS`）。
- 触发：网关在 `stream()` 入口抛 `GatewayBlockedError`（限流/熔断）时，`error_kind`=`"rate_limited"`/`"circuit_open"` 不在集合内（集合只有单数 `rate_limit`），`retryable=False` → 立即失败不重试，`remaining_open_s`/`retry_after_s` 形同虚设；熔断恢复依赖后续新请求探测。
- 修法：把 `rate_limited`、`circuit_open` 加进可重试集合（或归一为 `rate_limit`），`can_replay` 时按 `retry_after_s` 退避重试；补一条运行时重试分类单测锁住该行为。

### H3. InvoiceCenter 详情加载中/失败态被浅层列表数据静默掩盖
- 位置：`web/src/procurement/InvoiceCenter.tsx:85`（`const detail = detailQuery.data ?? selected ?? null`）。`selected` 来自列表接口（后端 `view(false)` 不含 `three_way`/`match_explanation`）。
- 影响：详情首次请求中或失败时 UI 无加载/错误提示，直接展示无三单对比的浅数据，用户误以为匹配已完成；详情接口失败时无重试入口。
- 修法：仿 `ProcurementWorkbench.tsx:426-435`，对 `detailQuery.isPending/isError` 渲染专属区块（错误给重试按钮），仅当详情数据存在时才回退 `selected`，回退期间标注「列表快照，正在加载完整对比」。

---

## 二、中危发现（关键 10 条）

### M1. 发票/合同状态列表过滤依赖 MySQL 大小写不敏感排序规则 ◐
- `InvoiceService.java:287-288`、`ContractService.java:178`：`findByStatusOrderByCreatedAtDesc(status.strip().toUpperCase(ROOT), …)` vs 落库小写 wireValue（`InvoiceStatus.wireValue()`="registered"…）。当前靠 `utf8mb4_0900_ai_ci` 才命中；换 `_bin`/`_cs` 排序规则即静默返回空列表（对比 Settlement/Order 大写枚举恰好自洽，此处是错误搬移）。
- 修法：统一按 `fromWire(status.strip()).wireValue()`（小写）过滤，去排序规则依赖；三处（InvoiceService/ContractService/SettlementService）对称修。

### M2. parser_version 跨侧错位：真实 Agent 模式落库为空串 ◐
- `internal_agent.py:184` 把 `parser_version` 放返回顶层；`InvoiceService.java:175` 从嵌套 `invoice` map 读 → 真实模式恒取 null→''（demo 的 `SyntheticAgentClient` 放在 invoice 内层，掩盖了问题）。契约 `InvoiceView.parser_version` 必填语义不满足。
- 修法：统一放 `invoice` 内层（推荐，与 demo 一致），三处对齐并加断言。

### M3. 三单匹配实际只用「PO vs 发票」，收货(GRN)数量未参与判定 ◐
- `ThreeWayMatcher.match(PurchaseSide, Invoice)`：`PurchaseSide` 只含 `quantity/landedTotal/expectedTaxRate`，无 `receivedQuantity`；`order.getReceivedQuantity()` 仅出现在 view 展示。与 README/页面「PO+收货 GRN+发票」表述不符：分批收货/超收时仍按 PO 数量判定。
- 修法（二选一）：把数量维度改用实收量比对（发票数量落在实收量容差内），或把 README/文案改为「PO（到货口径）vs 发票」避免夸大 GRN 参与度。

### M4. 合同「变更」实际只做状态往返，不含新条款/金额/交期 ◐
- `Contract.java:167-182` `requestChange()` 只把旧条款快照写进 change_history，不替换 clauses；`ContractDtos.ContractAction` 仅 `confirmed/notes`，无变更内容。CHANGE_REQUEST→APPROVE 后合同原封不动，「重新审批」无法体现变更。
- 修法：`request_change` 增加新金额/交期/条款入参 + `applyChange` 双快照校验（走 ContractConsistencyPolicy）后生效；或如实降级文案为「变更未落地」。

### M5. applyExplanation 无乐观锁防护，并发修改会以 500 冒泡 ◐（代码形态确认）
- `InvoiceService.java:186-199`：`findById`（无锁）→ `save(invoice)` 无 try/catch（对比 action 方法有 `catch OptimisticLockingFailureException`）。并发整改发票时版本冲突可能把 outbox 调度事务打成 500 且该命令不 terminal 不 defer。
- 修法：包一层捕获，重读最新版合并解释后重试一次或转 409 语义（同 contract）。M1 修法对 three 处 list 生效时一并统一。

### M6. 合同草拟结果一致性校验失败后无出口（死局）◐（代码形态确认）
- `ContractService.java:86-88`（`contract_already_exists` 用 `findByTaskId().isEmpty()` 永久挡） + `:133`（applyDraftResult 校验失败抛 409，此时 createDraft 已在其独立事务落库 DRAFT）。真实模式 Python 返回不一致金额/交期后，合同永久停在 DRAFT 且无法重新草拟/删除。
- 修法：提供 re-draft（覆盖草拟，仅 DRAFT）或 delete（仅 DRAFT）端点。

### M7. 半开(half-open)阶段不限制并发探测 ◐（双代理 ○）
- `gateway.py` `CircuitBreaker.state()` 窗口过期即 `half_open` 且 `allow()` 恒 True；`ProviderGateway.acquire()` 仅在 `state()==open` 拦截 → 恢复探测窗口内所有并发请求同时打到未恢复 provider，与「单探测」语义不符（late result 兜底但不严格）。
- 修法：加 half-open 单飞行探测位（一个在途探测 completion 前其余走降级/拒绝），补并发场景测试。

### M8. 稳态流量下 `circuit_closed` 事件刷屏 ○
- `gateway.py:239-249`：breaker.record 正常路径也返回 "closed"，只要样本到位每个请求都推 `provider_gateway.circuit_closed` 到 Kafka/Java。
- 修法：仅 open/half_open → closed 的**状态迁移**才发事件。

### M9. 心跳线程跨线程读网关快照（GIL 下陈旧值 + 字典迭代风险） ○
- `agent_service._heartbeat_loop`（daemon 线程）调 `gateway_snapshots()` 迭代 `self.gateways`，asyncio 主线程并发写；字典增删时可能 `dictionary changed size during iteration`。
- 修法：`snapshot()` 与 gateways 加线程锁或浅拷贝键列表。

### M10. 契约/代工与实现类型不一致 ·（契约层报告）
- `InvoiceActionInput` 数值字段：schema/web 声明 `string|null`，Java DTO 用 `BigDecimal`（wire 为 number）——契约严格校验器会拒绝实际 number。
- 修法：三侧统一为 `number|null`（或 Java 改 String 服务端解析）。

---

## 三、文档与产物不一致（已核实，建议尽快修正）

| # | 不一致 | 证据 |
|---|---|---|
| D1 | `docs/evidence/final-report-p1-p3.md`、README、p2-2 证据宣称「新增 3 份冻结评测」，其中 `frozen-evaluation-corrections.json` **在仓库中不存在**（git 未跟踪、frozen 目录无此文件）；导出脚本 `scripts/export_corrections_to_eval.py:20` 默认写到仓库根 | ✅ git ls-files + frozen 目录逐一确认 |
| D2 | `docs/evidence/p3-2-contract-management.md:18` 写「12 例合成合同」，实际 `frozen-evaluation-contract.json` 仅 **8 例**（ct-01..ct-08） | ✅ 实测 case_count=8 |
| D3 | `docs/evidence/README.md` 公开证据索引未收录 p1/p2-1/p2-2/p2-3/p3-1/p3-2/p3-3 及 final-report 新证据 | ○ 纪律代理报告 |
| D4 | 证据文档对合同状态机描述与实现不符：文档写 `CHANGE_REQUESTED/REJECTED` 终态、变更历史带 from/to，实现为 `CHANGE_REQUEST`、无 from/to | ◐ 与 H1/ M4 同源 |

> 说明：D1/D2 是本次升级会话产生的文档误差（corrections 评测产物未落盘即被写入文档）；建议在修复代码的同时订正以上文档。

---

## 四、低危 / 信息级摘要（不逐条展开，未复核 ·）

**Java**：`ContractService.action`/`InvoiceService` 的 `saveAndFlush` 在 try/catch 外（版本冲突落全局泛型错误）；`upload` 幂等检查在 artifact 落盘之后（重复上传产生孤儿制品）；`expectedTaxRate` 同一表达式调用两次；`V14.artifact_id varchar(36)` vs 实体 `length=32` 不一致；`SyntheticAgentClient` 数值缺失时产出空 `total_amount` 触发误导性 `invalid_invoice_field`；`IllegalStateTransition` 无全局 @ExceptionHandler（靠调用方先 can() 的隐式约定）。

**Python**：`agent_tools.py` 需求语义缓存 schema 版本硬编码 2；`_parse_attachment`「仅缓存校验通过结果」文档与行为不符（review 未清也 put）；`evaluate_contract.py` `case['id']*2` 是字符串拼接（虽无害）；`export_corrections_to_eval.py` exported_at 使输出非字节幂等；`contract_drafting` 阈值 `>=5000` vs 提示文案「大于 5000」、`float('inf')` 可触发提示；`invoice_parsing` startswith 别名可跨字段吞噬（`合计` vs `合计金额`）；每行每别名重编译正则（性能可接受）。

**Web**：两个新旗舰页面**零组件测试覆盖**；手工改单表单缺 `unit_price`（`InvoiceCenter.tsx:66,147-154,386`）；新页面对话框未接 `useEscape`/焦点管理（与既有模态不一致）；列表+详情恒 5s `refetchInterval`（终态也轮询，与 useRequestQueries 函数式口径不一致）；`ContractCenter` 与 `ProcurementWorkbench.csx:323` 内联重复状态文案（未来新增状态会静默兜底「草拟中」）；`three_way.grn.received_at` 空日期被 `String.valueOf` 成字符串 `"null"`。

**契约/评测**：`/corrections` 默认 size=50 vs 契约 20；`purchase_request_id` 语义随调用方（taskId 或 orderId）；`Idempotency-Key` 契约 required 与实现可选矛盾；`evaluate_invoice` 数字一致性校验用硬编码 sample_diffs（非真实 corpus）；`evaluate_contract` 条款评测为弱阳性样例（所有 case 同 6 个模板条款，precision/recall 恒 1.0，需负例才能区分）。

---

## 五、做得好的点（审核通过项）

- **纪律合规**：冻结资产（ApprovalService/ComparisonEngine/frozen-evaluation{,-ext}.json/golden）在 `df0a173..HEAD` 零 diff；秘密扫描无实质问题（无 .env 被跟踪、无明文密钥、provider key 走环境变量）；compose 仅映射 127.0.0.1:8741、其余服务不暴露端口；迁移 V1..V15 连续无缺号；web/dist 与 Java static 字节一致；基线测试改动均为合理适配。
- **确定性设计**：三单匹配金额全部 `compareTo`/显式 HALF_UP；Python 解析/起草无随机源、无 ReDoS；缓存键 `semantic:{scope}:{sha256}:{version}` + TTL + Redis 不可用 no-op；解析资源受限（5MB/500 行/20 页上限，xlsx 只读不解析公式）。
- **架构一致性**：AgentCommand 10 个操作名在 5 个声明点全对齐；新端点 path/method/字段/枚举在 Java↔web↔契约四侧一致；状态机四机统一注册 StateMachineRegistry + 乐观锁 + 409；审计事件齐全；付款 409 闸门有测试。
- **安全**：GatewayStatusView 心跳快照字段白名单脱敏且有单测；web 无 dangerouslySetInnerHTML，Agent/后端文本全部经 React 转义渲染；QuoteWorkspace 外链 `rel=noreferrer`。

---

## 六、修复优先级建议

1. **立即（影响正确性/可靠性）**：H1（变更驳回重置为 DRAFT）、H2（网关限流/熔断不重试）、H3（详情错误被掩盖）、M5（applyExplanation 乐观锁）、M6（合同草拟死局）、M1（状态过滤排序规则依赖）、M2（parser_version 错位）。
2. **尽快（一致性/文档可信度）**：M3（GRN 参与度表述）、M4（变更无内容）、M7/M8/M9（网关并发与事件）、M10（InvoiceActionInput 类型）、D1/D2/D3/D4（文档订正）。
3. **排期（体验/维护）**：手工改单补 unit_price、对话框 useEscape/焦点、refetchInterval 函数式、两旗舰页组件测试、半开单探测单测、`evaluate_contract` 负例、低危清单各项。

> 本次仅审核未修复。如需，可逐项按上述组件定位提交修复（每项修复需过既有门禁：ruff/pytest/`mvnw test`/`npm test+lint+build`/determinism）。
