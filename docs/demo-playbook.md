# 采价台演示手册

本手册使用合成报价验证本地采购闭环，不代表真实企业上线或未知版式准确率。

## 准备

```powershell
uv sync --all-groups --frozen
uv run python scripts/generate_procurement_demo.py --output output/procurement-demo

docker compose build
docker compose up -d --no-build
docker compose ps
```

只打开 [http://127.0.0.1:8741](http://127.0.0.1:8741)。不要访问或映射 Python 8742、MySQL 3306，也不要复用真实浏览器 profile 做自动验收。

核心目标：

```text
请采购白色 PE 快递袋，250×350 mm，60 微米，单色印刷，
数量 10000 个，最长交期 15 天，需要开票，送货到华东仓。
```

首次可上传 `华东优包报价单.xlsx`、`沪上包装报价单.pdf`、`星河包装报价单.pdf`。完整复核演示可上传 `output/procurement-demo/` 的 31 份报价。

## 五分钟闭环

| 时间 | 操作 | 可核对证据 |
|---|---|---|
| 0:00-0:40 | 新建采购对话并用 multipart 上传报价。 | Java 返回 202 operation ID；任务随后绑定 Session/Run；原件由 Java 保存 SHA-256。 |
| 0:40-1:30 | 查看结构化需求与报价来源。 | 星河包装供应商名约 55% 并进入人工复核；31 份场景另含缺失运费和冲突字段。 |
| 1:30-2:10 | 修正星河包装供应商名；为顺达包装补运费 650；确认冲突字段真实值。 | 每次修正增加 generation、保存人工 actor 并失效旧快照/待决审批。 |
| 2:10-3:00 | 确认 CNY/USD/EUR 固定汇率并开始比价。 | 31 份场景应为 19 家合格、12 家淘汰，推荐华东优包；错误物料不得入选。 |
| 3:00-4:00 | 选择供应商并二次确认。 | pending decision 与 Harness allow-once Approval 逐项绑定，Java 最终事务再次复核。 |
| 4:00-5:00 | 打开审批报告和运行审计，刷新页面。 | 报告显示 MySQL 供应商历史、采购订单草稿、供应商确认邮件、Run/Checkpoint/Approval 和证据哈希。 |

## 其他核心分支

### 全部淘汰与重开

收紧硬约束使全部报价淘汰。比较页必须隐藏供应商批准动作，只提供调整需求、补充报价、重新比价或填写原因后流标。流标报告不生成订单/邮件；“复制重开”创建新任务，可选择复制报价，旧任务保持不可变。

### V2 动态规格

使用 `output/procurement-scenarios/03-透明封箱胶带/` 的三份报价，并描述“3000 卷、长度 100 米、厚度 50 微米、12 天、开票”。Java 应把报价的 `100000 mm` 与需求的 `100 m` 匹配，淘汰 MOQ 10000 的北辰耗材，推荐嘉兴胶粘。

### Agent 中断

在已持久接受的操作后停止 Agent：

```powershell
docker compose stop agent
```

任务应显示 retryable，Java readiness 保持 UP，采购报告和已投影 Run 审计仍可读取，实时 `/api/runtime` 返回结构化 503。恢复后 outbox 使用原 operation ID 继续：

```powershell
docker compose start agent
docker compose ps
```

### 审批并发与重复

创建待决审批后修改需求或报价。旧 pending decision 必须变为 stale，迟到提交返回 stale approval。重复提交同一最终工具结果必须返回原决定，并且订单/邮件各只有一个。

## 故障预期

| 场景 | 系统行为 |
|---|---|
| 缺少 USD/EUR 汇率 | operation 标记 failed，Web 显示具体错误，任务恢复到 ready；补齐汇率后可重新分析 |
| Python 暂时不可用 | outbox 保留命令、任务 retryable、有界重试；不丢弃业务状态 |
| Java 重启 | MySQL 中 accepted/pending 命令继续；刷新恢复任务、报价、Session/Run 和修正 |
| Python 重启 | operation ID 幂等重放；同键同载荷返回原结果，同键异载荷返回 409 |
| 修改与批准竞争 | 最终事务锁定任务并复核版本/快照/输入哈希；只有一方成功 |
| 数据库事务失败 | 不留下部分任务、孤立决定或缺审计状态 |

## 可公开证据

- 字段抽取 617/620；
- 物料/规格匹配 31/31；
- 金额 31/31；
- 硬约束漏检 0/17；
- 不合格错误入选 0。

不要描述为“真实企业上线”“未知文档总体准确率”或未经真人对照证明的提效比例。
