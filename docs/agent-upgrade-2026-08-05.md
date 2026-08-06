# 采价台 Agent 升级建议 —— 未处理/待核实事项清单

> 核验日期：2026-08-06（新增一轮：秋招就绪审查）
> 核验范围：当前工作区代码、测试、CI 配置、冻结评测实现、已提交公开证据，以及 `output/proc-review-live-real/` 中一条真实模型运行记录的交叉核验。隔离 worktree 深读未能在当前 Git 重定向环境中完成，因此本文不把“子代理数量”作为证据。
> 状态说明：本文档只保留尚未处理或待核实的建议。2026-08-05 轮“已修复事项”（新对话工具阶段门控、Responses 官方状态保留、Chat 生成参数转发、公差默认值）仍按既有约定不重复列出；其 2026-08-05 遗留未决项全部保留在文末。2026-08-06 轮为本次新增，已实测确认的条目标记 `[已确认]`；依赖历史 checkout 或一次性运行记录的内容标为历史实测，不冒充当前可复现结论。**2026-08-06 第二轮：P0×3（审批摘要 bug、HEAD/工作区一致、CI 覆盖率）与死代码、CHANGELOG 口径已闭环，详见下方“已闭环”小节。**

### 2026-08-06 第二轮 · 已闭环（本轮实测）

> 本轮按“第一周冲刺”清单闭环了三个 P0 与死代码项。证据全部来自当前工作区实测，提交后 HEAD 与 CI 一致。

| 条目 | 处理 | 证据 |
|---|---|---|
| [P0] 审批摘要截断后仍被 JSON 解析 | `procurement/agent.py::_wait_for_approval` 改为只校验**完整参数的 `arguments_sha256`**（`arguments_sha256({"request_id", **selection})` vs 存储哈希），不再 `json.loads(arguments_summary)`；新增长备注回归测试 `test_procurement_approval_with_long_note_does_not_rely_on_truncated_summary` | 旧代码下该测试以 `409 采购审批参数不可验证` 失败；修复后通过；存储的 `arguments_summary` 仍 ≤500 字符但不再被解析 |
| [P0] HEAD 与工作区不一致 | 审查并提交全部工作区改动（核心、测试、web、web_dist、文档）；提交后 HEAD == 工作区 | 提交后完整测试 **219 passed / 1 skipped**；`git status` 无残留跟踪改动；detached worktree @ HEAD 复测同结果 |
| [P0] 干净 checkout 复现失败（Windows 行尾转换） | 新增 `.gitattributes`：`eval_truth.json`（`FROZEN_TRUTH_SHA256` 按字节校验）与 `web_dist/**`（确定性构建）标记 `-text`，禁止 checkout 时 LF→CRLF | 修复前 detached worktree @ HEAD 实测 **49 failed**（`load_frozen_truth` 哈希不符）；修复后同一干净 checkout **219 passed / 1 skipped / 80.02%** |
| [P0] CI 覆盖率门槛 77.08% < 80% | 补 `tests/test_security_and_verification.py`（sandbox/approval/VerificationLoop）与 `tests/test_tool_execution_helpers.py`（tool_execution 纯函数），并删除死代码以收缩分母 | `pytest --cov=agentharness --cov-fail-under=80` 实测 **80.02% PASS**；web job 本地 `npm test` 12/12、`npm run lint` 通过、`scripts/check_web_build_determinism.py` 字节一致，重建后的 `web_dist` 已提交 |
| [P2] 死代码：命令校验器指向已删除的 `shell` | 从 `contracts.py`（`VerificationCheck.kind` 去掉 `command`、删除 `command` 字段与 `ToolSpec.requires_approval`）、`engine/verification.py`（删除 `CommandRunner`/`_command_check`）、`engine/runtime.py`（删除 `governed_command` 与 `[verification command validator]` 过滤）整体移除该特性类 | `ruff check .` 通过；无任何测试引用已删路径 |
| [P1] CHANGELOG “isolated real-model acceptance evidence” 口径 | CHANGELOG `[Unreleased]` 措辞改为“单次真实模型全链路运行记录存在工作区，但尚未整理为公开可复现评测，因此不声明真实模型准确率/成本”；与 README / demo-playbook 现有诚实声明对齐 | `CHANGELOG.md` diff；README / demo-playbook 无需改动 |

**剩余未闭环**：真实模型记录的 transcript/run-report 整理进 `docs/evidence/`（P1）、CJK token 估算与 context-length 降级（P1）、两块 FAQ（P2）、同步工具事件循环审查（P2）、P3 卫生项，以及 2026-08-05 遗留项，仍按文末与冲刺表继续保留。

### 2026-08-06 第三轮 · 已闭环（本轮实测）

> 本轮闭环第 2/3 周清单中的代码与文档项；除 Docker 镜像构建外均有本地实测证据。

| 条目 | 处理 | 证据 |
|---|---|---|
| [P1] Token 估算 `len/4` 中文低估 | `engine/context.py::estimate_tokens` 改为 CJK 感知：CJK 字符按 1 token/字符、ASCII 按 4 字符/token | 新增 `test_estimate_tokens_is_cjk_aware`；旧值“中文中文=2”现为 4；“采购10000个PE白色快递袋”=10；全套 222 passed |
| [P2] context-window-exceeded 无降级路径 | 适配器与运行时把 400/413/422 及 context-length 类消息分类为 `context_length`；运行时对无输出的 context_length 错误**缩减一次预算**（`max_context_tokens // 2`，下限 8k）并用更小上下文重新 plan 后重试；`provider_retry` 事件记录 `context_shrunk_to` | 新增 `test_context_length_error_shrinks_budget_and_retries_once`（100k→50k、2 次尝试、completed）与 `test_classify_error_detects_context_length`；`test_provider_retry.py` 11 passed |
| [P2] 同步工具事件循环审查 | 逐个审查 4 个白名单工具：`capture_requirement`/`execute_analysis` 的解析/比价流水线已在 `asyncio.to_thread`；`read_request`/`approve_supplier` 仅为有界 SQLite/JSON 操作，无长时间同步 I/O 或 CPU | 审查结论记录于本表；无需代码修改 |
| [P3] 文档漂移 | README 的 `MAX_TOKENS`/`MAX_STEPS` 由 100000/12 改为与代码默认一致的 **50000/20** | `README.md` diff；`procurement/agent.py` 默认值即 50000/20 |
| [P3] “推荐稳定率”口径 | 从 `AuditView.tsx` 与 README 评测输出列表移除该同义反复指标（确定性复算自洽 ≠ 输入扰动稳定性），不再作为招牌指标；`evaluation.py` 原始 JSON 保留但不上屏 | `npm test` 14 passed；`npm run lint` 通过；重建后的 `web_dist` 已随本批提交 |
| [P3] AppErrorBoundary 无测试 | 新增 `web/src/AppErrorBoundary.test.tsx`（正常渲染 + 抛错兜底） | web 测试 12 → **14 passed** |
| [P3] Docker / 一键启动 | 新增 `Dockerfile`（uv slim 镜像 + `uv sync --frozen --no-dev --no-editable`）、`.dockerignore`、`docker-compose.yml`；README 增加启动说明 | `uv sync --frozen --no-dev --no-editable` 在全新 scratch venv 实测成功且 `agentharness` 可导入；**本机 Docker daemon 未就绪，镜像未实际 build**（如实记录，非已验证项） |
| [P2] 两块 FAQ | `docs/demo-playbook.md` 面试追问新增“为何不用 LangGraph/扣子/OpenAI Agents SDK”与“转型删掉的能力怎么加回”两段可防御回答 | `docs/demo-playbook.md` diff |
| [P1] 评测证据整理（口径部分） | 新增 `docs/evidence/live-real-run-report.md`：单次真实模型（deepseek-v4-flash）全链路运行记录，含 run_id、6 回合、18,690 tokens（7,936 缓存）、137 事件、终态 `passed/verified`、费用 null（未配置价格），并明确“非公开可复现评测、不构成准确率/成本证据”；`docs/evidence/README.md` 登记 | 数据直接取自 `output/proc-review-live-real/agentharness.db`（runs/events/tool_invocations/approvals）；与 CHANGELOG/README 诚实声明一致 |

**剩余未闭环**：受预算约束的真实模型多轮评测（P1，需预算与更多运行数据）、2026-08-05 遗留的独立模型评审 / 故障转移 / 精确事实检索（均为“待核实/保留”，不接入）、P3 英文文档；Docker 镜像 build 待 daemon 可用后补验。

## 2026-08-06 轮 · 秋招就绪审查结论

> 一句话结论：运行时与产品工程深度在实习中位数之上（子系统评分 7–8/10）。本轮（2026-08-06 第二轮）已将 4 个硬伤中的 3 个闭环：**CI 覆盖率门槛（80.02% ≥ 80%）、当前 HEAD 与工作区一致（提交后 219 passed / 1 skipped）、招牌人工审批路径 bug（改绑完整参数 `arguments_sha256` + 长备注回归测试）**；第 4 个“冻结评测 0 次调用模型”属口径问题，CHANGELOG 已同步诚实化，但把单次真实模型运行整理成公开可复现评测仍为 P1 未闭环。其中评委评分和“2–3 周修完”属于主观判断，不作为仓库实测证据。
>
> 已完成一条真实模型全链路运行记录（`output/proc-review-live-real/`，截图在 `docs/evidence/live-real-*.png`）：deepseek-v4-flash 下 create → capture（step0 因空汇率表被确定性校验拒绝 → step1 自纠成功）→ require_human 停等人工 → 修正 → 比价快照 → allow_once 一次性审批 → 终态 `passed / verified: true`。记录显示 6 模型回合、18690 tokens（7936 缓存）、137 事件。**这证明链路在该次运行中走通，但运行目录被 gitignore，证据尚未整理成仓库内可独立复现的公开评测；以下仍有呈现层和可复现性问题。**

### [已确认][P1] 冻结评测 0 次调用模型，与 CHANGELOG 的真实模型证据声明存在口径冲突

复算确认：冻结评测 `model_usage.calls=0`（真值回放，`evaluation.py`），“617/620”测的是**确定性管线 + 模拟人工门**，LLM 未参与。README / demo-playbook 已诚实声明这一点；但 `CHANGELOG [Unreleased]` 声称存在“isolated real-model acceptance evidence”，而 `output/` 被 gitignore，当前公开证据目录没有对应 transcript、run report 和完整 model_usage。仓库外运行记录中确有一条 deepseek-v4-flash 真实链路，并已留下两张工作区截图，但目前不能由干净 checkout 独立复现，因此不能把冻结评测数字或该单次运行宣传为真实模型准确率。

- 修法：把记录整理为脱敏 transcript + run report + 诚实的 model_usage，并提交到 `docs/evidence/`（**未闭环**）；~~同步修掉 CHANGELOG 与 README / demo-playbook 的矛盾~~ **已闭环：CHANGELOG [Unreleased] 已改为诚实措辞，与 README / demo-playbook 一致**。发布两个诚实分层：确定性管线 617/620（0 模型调用）vs agent 编排单次运行的 X/Y（真实调用、成本未知或按实际价格计算），不要将单次链路外推为总体准确率。

### [已确认][P1] Token 估算采用 `len/4`，中文场景可能明显低估，且没有 context-length 专门降级

[context.py](src/agentharness/engine/context.py) `estimate_tokens` 用 `(len+3)//4`（≈4 字符/token）。这不是对所有中文输入都能固定量化为“低估 3–4 倍”，但对当前中文采购提示和历史文本可能明显低估，导致压实触发滞后、上下文预算失真。运行时也没有针对 provider `context-length` 错误的专门缩减预算/重新压实路径；本地 planner 自身仍会先按配置预算拒绝超限输入。

- 修法：使用目标 provider 的 tokenizer 或经实测校准的 CJK 感知估算；补 context-length 错误分类与一次受预算约束的缩减/重试路径，并用真实 provider 错误样本回归测试。
- 秋招价值：对中文 LLM 公司（智谱/月之暗面/DeepSeek/百炼）这是最可能的旗舰面试题，现在是明知错的心智算法。

### [待核实][P2] 定位叙事缺"为何不用 LangGraph / 扣子 / OpenAI Agents SDK"的回答

面向扣子/百炼/通义 agent 平台岗几乎是必问题，仓库内无答案。差异化论点应是"可验证 + 可治理"（ground-truth 可判定、审批绑精确选择、审计闭环），而非又一个框架壳。需写入 demo-playbook 成为可防御的回答。

### [待核实][P2] 转型删除的通用 agent 能力（MCP/记忆/多智能体/浏览器/Shell）无访谈预案

面试官会问"这些怎么删了、怎么加回来"。需准备一段"为什么收敛为 4 个白名单工具 + 如何加回 MCP/记忆/多智能体"的防御性回答，当前文档未覆盖。

### [待处理][P2] 需审查同步工具是否在 `async def run` 内阻塞事件循环

工具协议要求 `async def run`，且采购分析内部已经把同步分析流水线放入 `asyncio.to_thread`。因此不能把所有 `await tool.run(...)` 统一判定为 bug；真正需要核验的是每个工具实现是否在 async 函数内直接执行长时间同步 I/O 或 CPU 工作。若存在，应针对该工具使用线程池或改为真正异步实现，并补充 heartbeat、取消和 timeout 回归测试。

### [待核实][P2] context-window-exceeded 无降级路径

规划器只按自身 token 预算适配（默认 100k），网关真实窗口更小时以不可重试 provider 错误硬失败。需分类 `context_length` 错误、缩小预算重试一次，而非直接失败。

### [保留][P3] 次要口径与卫生项

- "推荐稳定率"指标是同义反复：`evaluation.py` 把确定性 compare 跑 5 次报自洽，不是输入扰动下的稳定性，别当招牌指标。
- 数字文档漂移：`.env.example`（MAX_TOKENS 50000 / MAX_STEPS 20）vs README（100000 / 12）。
- 无英文文档（投国际化/外籍评审岗位有影响）。
- `web/src/AppErrorBoundary.tsx` 无测试；web 前端 12 个测试对产品面偏薄。
- 无部署形态（Docker / 一键启动），"能跑"与"能交付"是两回事。

### [待处理][P2] 秋招定位建议（策略结论，非 bug）

不要说"做了 agent 框架"（简历词条饱和、不可验证），也不要说"做了采购应用"（听起来非 agent）。建议一句话定位：

> **受治理、人在环路的采购比价 agent 系统**——4 个白名单工具的薄 agent 编排确定性报价管线；可靠性栈（checkpoint/resume、绑精确选择的审批、不可变快照、审计、SHA-256 固定可复算评测）让它"可信到能碰真钱"。

简历双故事：抬头写采价台（证明交付 + 诚实评测），深度行写 agent runtime（流式 function-call 归一化、上下文压实、租约恢复、校验循环）。面试开场主张“agent 只有在你能验证和治理它时才有用”。**主动承认**“617/620 测的是确定性管线（0 模型调用）；另有一条真实模型单次运行记录，但尚未整理成公开可复现评测”——这份诚实在中国 LLM 公司是差异化。

### [待处理][P2] 2–3 周冲刺顺序

| 周 | 事项 | 对应条目 |
|---|---|---|
| 第 1 周 | ~~提交工作区使 HEAD 绿仓；修审批摘要 bug + 长备注回归测试；CI 两 job 变绿；清死代码~~ **已完成（2026-08-06 第二轮）** | P0×3 + 死代码 |
| 第 2 周 | ~~校准 CJK token 估算并补 context-length 降级；逐个审查工具是否阻塞事件循环；整理单次真实模型记录进 `docs/evidence/`（口径部分）~~ **已完成（2026-08-06 第三轮）**；受预算约束的多轮真实模型评测仍待预算与更多运行数据 | P1 + P2 |
| 第 3 周 | ~~两段 FAQ；Docker/一键启动；文档漂移；删"稳定率"口径；AppErrorBoundary 测试~~ **已完成（2026-08-06 第三轮，Docker 镜像 build 待补验）**；英文文档与其余 P3 卫生项仍保留 | P2/P3 剩余 |

## 2026-08-05 遗留（继续保留）

### [保留][P1] 把采购验证 `max_retries` 调到 1–2

原文观察到采购 Agent 当前 `max_retries=0` 是事实，但"因此模型从不自修"不准确：通用 `VerificationLoop` 已支持 retry 和 `[verification_feedback]`，并有对应测试；采购审批完成后还存在专门的最终标记纠正路径。

更重要的是，采购验证当前要求 `procurement_approve_supplier` 成功，而审批必须等待采购员人工选择。分析阶段先得到比价结果、再进入人工审批是正常路径；直接把 `max_retries` 调高会在每次正常等待审批时额外调用模型，甚至诱导模型越过人工边界。应先做"分析阶段验证"和"审批阶段验证"的状态拆分，再考虑重试次数。本次保留现状。

### [待核实][P2] 独立模型评审

`VerificationPolicy` 和 `_ai_check` 已存在，但采购请求目前只配置了 deterministic output validator，根本没有启用 AI validator。即使注册第二 provider，当前 `_ai_check` 也只把目标、候选输出、步骤和工具名发给评审者，不包含报价原文、字段证据或比价快照，因此不能声称它已经能"对照采购证据"。

启用前必须明确：

- 评审输入的最小事实快照及脱敏规则；
- 第二模型的独立性、成本上限和失败时人工路径；
- 评审失败是否只影响解释，还是能阻止审批；
- 对已有持久化 Run 的兼容和报告 schema。

### [待核实][P2] 模型自动故障转移

从主模型切换到备用真实模型尚未证明安全；从真实模型静默切换到 `procurement_fake` 更不能称为"无损"，因为两者的提示遵循、工具选择、模型输出和用户信任语义不同。Provider 重试目前按同一 provider 设计，若要故障转移，应先定义 Run 级 provider 变更审计、费用归属、部分输出重放和人工确认规则。本次不接入。

### [保留][P2] 精确事实检索上下文段

采购 service 的报价、历史、快照和确定性复算已经通过白名单工具按数据库事实读取；没有证据证明摘要已经导致数字错误。把采购领域事实直接耦合进通用 `ContextPlanner` 会扩大核心运行时边界。若后续实测发现模型重复调用或误读，再以独立的采购事实工具/证据段设计，不把业务规则写入通用摘要器。

### [待核实][P3] "没有过程性叙述"和"一次性无状态澄清"

运行数据库确实显示两轮模型输出很少，且第二轮以 `require_human` 结束；但这不能证明产品澄清"无状态"：`ProcurementAgent.resume` 会从持久化 Run 恢复，前端也提供补充信息入口。过程性叙述已加入提示词，但模型是否稳定输出仍需重复实验验证，不把它当作采购正确性保证。

## 后续方向

- 若换模型后出现首轮不再调用 `procurement_capture_requirement` 的回退，再评估 provider 原生 `tool_choice` 及不支持该字段网关的回退。
- 只有在事实快照、独立评审输入和人工边界明确后，才评估 AI 交叉验证（对应"独立模型评审"）。
- 故障转移与通用事实索引暂不进入采购生产路径（对应"模型自动故障转移"与"精确事实检索上下文段"）。
- 新增：`2026-08-06` 轮 P0 项（审批 bug / HEAD 红仓 / CI 覆盖率 / 评测口径）应在下次核验前闭环，直接决定秋招投递时仓库的"可复现健康度"。
