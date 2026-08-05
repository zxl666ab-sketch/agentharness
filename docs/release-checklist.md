# 采价台本地发布检查清单

从仓库根目录运行。不得覆盖用户改动，不得提交 `.env`、数据库卷、密钥、完整日志、Playwright profile/trace 或真实报价。

## 1. Python

```powershell
uv sync --all-groups --frozen
uv run ruff check .
uv run pytest --cov=agentharness --cov-report=term --cov-fail-under=80 -q
uv build
```

必须保留解析器、Runtime、Provider、Run、Checkpoint、Approval、工具治理和 internal-only 安全测试。Python 不得重新出现公共采购 Router、业务 Service、costing 或采购 Repo。

## 2. Java 与 PostgreSQL 17

```powershell
Set-Location procurement-service
.\mvnw.cmd test
Set-Location ..
```

Testcontainers 必须实际启动 PostgreSQL 17。验证 BigDecimal 精度、税费、汇率、MOQ、交期、V1/V2 动态规格、排序、规范 JSON/hash、Artifact 路径、失效、审批状态机、事务回滚、乐观锁、幂等、重复调度、响应丢失和重启恢复。

## 3. Web

```powershell
Set-Location web
npm ci
npm test
npm run lint
npm run build
Set-Location ..
uv run python scripts/check_web_build_determinism.py
```

`web/dist` 必须被忽略。不得恢复 `src/agentharness/web_dist` 或 Python Web 打包逻辑。

## 4. 冻结契约

```powershell
uv run python scripts/evaluate_procurement.py run --output output/procurement-evaluation-v3
uv run python scripts/evaluate_procurement.py verify --input output/procurement-evaluation-v3/raw-results.json
```

必须满足：

- 31 份、6 种版式，真值 SHA-256 `63647f520bff1ab20e9215cc65e1b246a6f27fcf88cdb226fe7eae72fd6c1ffb`；
- 字段抽取 617/620；
- 物料/规格匹配 31/31；
- 金额 31/31；
- 硬约束漏检 0/17；
- 不合格错误入选 0；
- Java `FrozenComparisonContractTest` 与 `contracts/golden/frozen-comparison-v3.json` 一致；
- Decimal、规范 JSON UTF-8 字节和 SHA-256 跨语言一致。

## 5. Compose

在 Windows 中文路径遇到 BuildKit header 错误时使用：

```powershell
$env:DOCKER_BUILDKIT='0'
docker build -f Dockerfile.agent -t caijiatai-agent:0.4.0 .
docker build -f procurement-service/Dockerfile -t caijiatai-procurement:0.4.0 .
docker compose up -d --no-build
docker compose ps
```

检查：

- 三个服务 healthy；
- 只有 `127.0.0.1:8741->8741` 映射宿主机；
- Java `/api/health` 报告版本 0.4.0、API Schema 11、数据库 ready 和独立 Agent 状态；
- Java readiness 在 Agent 停止时仍为 UP；
- Python internal-only 无 Token 返回 401，有效 Token 才能访问 Runtime；
- Java Host/Origin 边界拒绝非本地生产请求。

## 6. 隔离无头浏览器

使用独立、无头 Playwright context，不连接或复用用户 Chrome tab/profile。桌面 1440×900 与移动 390×844 均验证：

1. 正常批准：创建、上传、复核、比价、选择、allow-once Approval、批准报告。
2. 低置信度：星河包装供应商名和顺达包装运费在修正前阻止比价，修正后归零。
3. 31 文件 multipart：返回 202，最终保存 31 份报价。
4. 全部淘汰：只能调整、补报价、重比或带原因流标；报告无执行草稿。
5. 复制重开：新任务可选择复制报价，旧终态不变。
6. 刷新/Java 重启：任务、附件、报价、Session、Run、修正和状态恢复。
7. 重复审批：只存在一个正式决定、一个订单和一个邮件。
8. 并发失效：修正与审批竞争时旧审批 stale，迟到结果被拒绝。
9. Agent 中断恢复：accepted operation 不丢失，恢复后沿原 operation 继续。
10. Runtime 降级：Agent 停止时业务报告 HTTP 200，实时 Runtime 结构化 503。
11. V2 动态规格：标签/key 映射和 `µm/mm/cm/m` 单位换算正确。
12. 异步失败：错误可见且任务不永久停在 analyzing。
13. SSE：连接超过 30 秒不被服务器超时，保留 ID、Last-Event-ID、心跳和重连。
14. 控制台无应用错误；文字无溢出或重叠；核心控件在两种视口可见可用。

批准报告必须可见 2 个执行 Artifact 和 PostgreSQL 供应商历史。截图、trace 和日志只写临时/忽略目录，并在验收后删除。

## 7. 故障与事务

- Python 暂时不可用：已持久接受请求保持 202/outbox retryable；未接受或实时代理才返回 503。
- 响应丢失：新 worker 使用同 operation ID 获取原结果。
- 同幂等键同载荷返回原结果；异载荷返回 409。
- 需求/报价修正原子失效快照与 pending decision。
- 最终提交锁定并复核任务版本、快照、输入哈希、资格、审批摘要和日期。
- PostgreSQL 回滚不留下部分业务状态；乐观锁冲突返回 409。
- Java Artifact Store 拒绝路径穿越并验证 SHA-256。
- Python 关闭时采购报告仍返回 200 且明确 `runtime_evidence_status=unavailable`。

## 8. 最终清理与启动

- 检查 `git status` 和所有新增路径所有者；
- 删除本次任务创建的 `.playwright-cli`、截图、trace、日志、临时数据库和演示输出；
- 不删除归属不明的预存用户数据；
- 清理测试容器后用新 PostgreSQL/Agent/Artifact 卷完成一次最终 Compose 闭环；
- 最后保持 `docker compose ps` 三服务 healthy，并确认 [http://127.0.0.1:8741](http://127.0.0.1:8741) 可访问。

旧 SQLite 采购数据只可归档，不自动导入或双写。任何未验证的 Compose/Testcontainers/浏览器项都不能以单元测试绿灯替代。
