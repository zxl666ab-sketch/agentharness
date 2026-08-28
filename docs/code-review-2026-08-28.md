# 项目代码审查报告

审查日期：2026-08-28
审查范围：Java 业务主机（procurement-service）、Python Agent 微服务（src/agentharness）、React 前端（web）、工作区卫生
审查方式：全量静态审查 + 三套测试实跑 + 关键缺陷编写复现脚本验证

---

## 一、总体结论

工程质量明显高于同类求职展示项目。README 声称的核心能力——三层防护（幂等/乐观锁/分布式锁）、HMAC 双侧签名、审计留痕、BigDecimal 精度纪律、Agent 边界受限——**逐条在代码中找到了对应实现，没有发现吹牛**。

但有两个真实缺陷会破坏其中两个卖点，其中一个是可复现的严重可用性缺陷。

| 维度 | 结论 |
|---|---|
| 测试健康度 | 全绿（Python 317 / Java 167 / Web 111） |
| 安全基本面 | 干净：无硬编码密钥、无注入、无路径穿越、无危险调用 |
| 代码纪律 | 好：BigDecimal 用 compareTo、HMAC 常量时间比较、分布式锁 Lua 条件释放 |
| 并发正确性 | **有缺陷**：熔断恢复存在可复现死循环 |
| 状态机引擎 | **有缺陷**：配置可静默覆盖、生产流转表零测试 |
| 工作区卫生 | **有隐私风险**：16 个脚本硬编码真实姓名与本机路径 |

---

## 二、测试实跑结果

| 套件 | 命令 | 结果 |
|---|---|---|
| Python | `uv run pytest -q` | **317 passed, 1 skipped**（111s） |
| Java | `./mvnw.cmd test` | **167 tests, 0 failures, 3 errors** |
| Web | `npm test -- --run` | **111 passed（16 文件）** |

Java 的 3 个 error 全部是 `MySqlIntegrationTest` / `KafkaTransportTest` / `DemoSeedIntegrationTest`
的 Testcontainers 报 `Could not find a valid Docker environment`——本机 Docker Desktop 未运行，
**属环境依赖，非代码缺陷**。启动 Docker 后这 3 个应能恢复。

Web 111 个测试满足 `ORIGINAL_REQUEST.md` 的验收标准（原要求 13 文件 / 80+ 测试）。

> 附注：`npm test` 墙钟耗时 15 分钟，但 vitest 自报 `Duration 24.88s`（其中
> `environment 232s`）。约 14 分钟开销在 npm/vitest 环境准备之外，建议排查是否为
> npm registry 探活或 Windows 文件监听开销——影响开发体验，非正确性问题。

---

## 三、严重问题

### S1. 熔断器半开探测恢复失效（可复现，已验证）

**位置**：`src/agentharness/providers/gateway.py:387-396`（`GatewayAdapter.stream`）

**问题**：`GatewayBlockedError` 继承自 `ValueError`（gateway.py:40），因此会被
`except Exception: outcome = "error"` 捕获。但 `GatewayBlockedError` 代表的是
**本地网关拒绝**（限流 / 熔断未开），请求根本没打到 provider，却被当成 provider 故障
计入失败率并触发熔断续期。

```python
# gateway.py:375-396
await self.gateway.acquire()          # 此处抛 GatewayBlockedError(rate_limited)
outcome: str | None = None
try:
    async for item in self.inner.stream(request): ...
except asyncio.CancelledError:
    outcome = None
    raise
except Exception:                     # ← GatewayBlockedError 落进这里
    outcome = "error"                 # ← 本地限流被记为 provider 失败
    raise
finally:
    self.gateway.release()
    if outcome is not None:
        self.gateway.record(outcome == "ok")   # ← record(False) 续期熔断
    else:
        self.gateway.abort_probe()             # ← 只有取消路径才释放探测位
```

**复现**（脚本 `output/repro_gateway_probe.py`，已实跑）：

```
[1] 两次失败后 state = open          (期望 open)
[2] 推进 61s 后 state = half_open    (期望 half_open)
[3] 探测请求被本地拒绝: code=circuit_open
[4] 误记失败: half_open -> open, 剩余熔断 60.0s
    stats.failures = 3  (本地限流本不该计入 provider 失败)
    !! 一次本地限流把熔断又续了 60s —— 恢复被无限推迟
[5] 正确路径(只 abort_probe): state=half_open, failures=2
```

**影响**：只要半开探测的那一刻令牌桶恰好没有令牌，探测就被判失败、熔断续期 60 秒；
下次半开再撞上限流，再续 60 秒。这是**自我强化的死循环**——一旦触发，provider 即使
已经恢复，网关也可能长时间无法闭合。

这一条直接打脸 README 第 90 行的宣称：「失败率熔断（30s 窗口 >50% → 熔断 60s，
**半开探测恢复**）」。面试官追问「熔断器怎么恢复」时会踩雷。

**修复建议**：在 `except Exception` 之前单独捕获 `GatewayBlockedError`，
既不记失败也不消耗探测位：

```python
except GatewayBlockedError:
    self.gateway.abort_probe()   # 本地拒绝与 provider 健康无关
    raise
except asyncio.CancelledError:
    outcome = None
    raise
except Exception:
    outcome = "error"
    raise
```

同时建议在 `gateway.py:259-261` 的 `acquire()` 内部 `except BaseException` 分支中
补上 `self._breaker.abort_probe()`，保证任何异常路径都不泄漏半开探测位。

---

### S2. 合同状态机 REJECT 目标被静默覆盖

**位置**：`procurement-service/src/main/java/com/caijiatai/procurement/contract/ContractStateMachineConfig.java:26-27`

```java
.permit(ContractStatus.CHANGE_REQUEST, ContractEvent.REJECT, ContractStatus.EFFECTIVE)
.permit(ContractStatus.CHANGE_REQUEST, ContractEvent.REJECT, ContractStatus.EXECUTING)
```

同一个 `(from, event)` 注册了两次。`StateMachine.Builder.permit` 内部是
`table.get(from).put(event, new Transition<>(to, action))`（`StateMachine.java:73`），
第二个静默覆盖第一个，引擎认定的目标恒为 `EXECUTING`。

**与文档矛盾**：README 第 94 行声称「变更驳回恢复变更前状态」，但引擎表里
`CHANGE_REQUEST + REJECT` 的固定目标是 `EXECUTING`，无法表达"恢复变更前状态"
（可能是 EFFECTIVE 也可能是 EXECUTING）。

**为何目前没引爆**：`ContractService` 丢弃了 `transition()` 的返回值，真实状态由实体
`Contract.reject() → previousStatus()` 从 `changeHistory.from_status` 取得。也就是说
**引擎在合同场景只当守卫，不是流转权威**——这本身削弱了"注册式状态机引擎"的叙事。

**修复建议**：
1. `StateMachine.Builder.permit` 对重复的 `(from, event)` 直接抛异常（fail-fast）；
2. 合同 REJECT 改为以引擎目标为准，并加断言校验与实体 `previousStatus()` 一致，
   让引擎真正成为流转权威。

---

## 四、中等问题

### M1. 生产状态机流转表零测试覆盖

`StateMachineTest` 只测合成的 A/B/C 状态机；`ContractChangeTest` 直接调用实体
`contract.reject()`，绕开了引擎。因此 S2 这类配置错误**测试发现不了**。

建议对 order / settlement / invoice / contract 四张生产流转表补断言测试，
至少覆盖：合法流转可达、非法流转抛 409、`(from,event)` 无重复注册。

### M2. 比价日期解析无异常保护

`ComparisonEngine.java:183`：

```java
if (!deadline.isBlank() && asOf.plusDays(leadDays).isAfter(LocalDate.parse(deadline))) {
```

`LocalDate.parse(deadline)` 未捕获 `DateTimeParseException`，而紧邻的
`valid_until`（175 行）做了 try/catch → warning。若 `required_delivery_date` 是脏数据
（如 `2026/01/15`、`15天内`），会让**整次比价 500**，而不是降级为警告。

建议与 175 行保持一致，catch 后加 warning 而非中断。

### M3. 语义缓存统计无锁

`semantic_cache.py:114-139` 的 `self._stats["hits"] += 1` 等自增无锁保护，
而 `gateway.py` 的同类统计用了 `threading.Lock`（gateway.py:208）。
Agent 服务为多线程，统计值可能漂移。属可观测性问题，不影响正确性，但与同仓库
另一处实现不一致，建议统一。

---

## 五、工作区卫生（建议优先处理）

### H1. 根目录 16 个临时脚本硬编码真实姓名与本机路径 ⚠️ 隐私风险

以下**未跟踪**脚本直接写死了个人身份信息：

```
update_with_git.py              C:\Users\example\Downloads\候选人2027届随时到岗.docx
apply_exact_modifications.py    候选人2027届随时到岗_格式完全一致优化版.docx
run_exact_replace.py            候选人_河南城建学院_格式完全一致.docx
generate_exact_date_docx.py     候选人_河南城建学院_精修版.docx
check_all_p.py / check_exact_template.py / check_target_runs.py / dump_exact_runs.py
inspect_exact_doc.py / inspect_heading.py / inspect_pbr.py
modify_exact_runs_in_place.py / print_run_lens.py
generate_interview_guide.py / generate_interview_qa_docx.py / generate_refined_resume.py
```

目前它们都是 untracked，**尚未进入 git 历史**。但只要一次 `git add .`，
真实姓名 + 学校 + 本机用户名就会被推到公开仓库。对一个求职展示项目这是硬伤。

**建议**：
1. 把这 16 个脚本移出仓库（或移入 `tools-local/` 并加入 `.gitignore`）；
2. 若需保留，改为 `argparse` 接收路径参数，不写死绝对路径；
3. 顺便确认还没有任何一次提交包含这些文件（`git log --all -- '*_exact_*.py'`）。

这 16 个脚本也是 `ruff check` 报 **73 个错误**的主要来源（导入未排序等）。
清理后 ruff 应恢复干净。

### H2. 工作区处于中间态

`git status` 显示 20+ 文件已修改未提交，且包含删除操作：
- 删除 `ReferencePriceService.java` 及其测试
- 删除 `frozen-evaluation-ext.json`
- 删除 `web/pnpm-lock.yaml`、`web/pnpm-workspace.yaml`
- 修改 `frozen-evaluation.json`（冻结评测基线）

冻结基线被改动尤其需要谨慎——README 的 617/620 指标依赖它。
建议确认这些改动是有意为之后整体提交，避免冻结数据处于半改状态。

---

## 六、审查中确认没问题的部分

这些是逐一验证过的，不是"没仔细看"：

- **无硬编码密钥**：`.env` 已被 `.gitignore` 正确排除；`git grep -E "sk-[A-Za-z0-9]{20,}"`
  跨全部历史命中项**全是测试用例里的假数据**（`test_redaction.py`、
  `test_internal_token_security.py`），无真实泄露。
- **无 SQL 注入**：全 JPQL 命名参数，无 `nativeQuery` 与字符串拼接。
- **无路径穿越**：`ArtifactStore` 正则白名单 + `startsWith(root)` 双重校验。
- **无危险调用**：Python 侧无 `eval` / `exec` / `pickle` / `subprocess`。
- **HMAC 比较安全**：双侧均常量时间（Python `hmac.compare_digest`、Java `MessageDigest.isEqual`）。
- **BigDecimal 纪律良好**：金额一律 `compareTo` + 显式 scale，未发现误用 `equals`。
- **分布式锁释放安全**：Lua 条件释放 + 请求标识，不会误删他人锁；Redis 不可用有降级路径。
- **测试不空转**：无 `@Disabled` / xfail / 仅 `assertNotNull` 的弱断言。
- **`@Transactional` 无自调用失效问题**。
- **Agent 边界属实**：合同草拟是确定性模板（非 LLM 直接产出）；发票差异解释仅注入
  结构化 diff 中的数字；正式决定由 Java 重算比价并校验后才落库。README 的说法站得住。

---

## 七、建议的修复顺序

1. **S1 熔断恢复**（最高优先级，1 处代码 + 1 个回归测试，直接支撑 README 卖点）
2. **H1 清理含个人信息的脚本**（防止误提交，5 分钟的事）
3. **S2 + M1 状态机**（permit fail-fast + 补生产流转表测试，支撑"平台可扩展"叙事）
4. **M2 日期解析**（对齐相邻代码，一行 try/catch）
5. **M3 统计加锁**、**H2 整理未提交改动**（可择机）

---

## 八、2026-09-03 修复状态

| 编号 | 问题 | 修复批次（2026-09-03） | 状态 |
|---|---|---|---|
| S1 | 熔断器半开探测恢复失效（`providers/gateway.py`） | Python 网关在限流/异常路径释放 `_probe_inflight`（probe release） | ✅ 已修复待复验 |
| S2 | 合同状态机 REJECT 目标被静默覆盖（`StateMachine.java:73` put） | 状态机改为 fail-fast：重复注册/覆盖直接抛错 | ✅ 已修复待复验 |
| M2 | 比价日期解析无异常保护（`ComparisonEngine.java:183`） | ComparisonEngine 增加 parse 守卫（异常降级为受控错误） | ✅ 已修复待复验 |
| M3 | 语义缓存统计无锁（`semantic_cache.py:114-139`） | stats 读写纳入锁保护 | ✅ 已修复待复验 |

> 注：状态按 2026-09-03 修复批次指令登记，以活体复验为准。截至本批文档更新时，工作区实测 **S2/M2 已在码**（`StateMachine.permit` putIfAbsent+抛错、`ComparisonEngine` parse try/catch 均在）；**S1（gateway 限流路径释放探测位）与 M3（semantic_cache stats 加锁）尚未见于当前源码**——对应修复仍在并行落库中，复验前请勿视为已生效。M1（状态机流转表测试）与 H1（根目录含姓名/本机路径脚本）不在本批文档批次内。
