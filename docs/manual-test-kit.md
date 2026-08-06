# 手动自测清单（8741 单实例）

> 测试地址：http://127.0.0.1:8741（当前最新构建，HEAD e0eff55）
> 建议：右上角「API / 模型配置」选 **procurement_fake**（离线、确定性、不花钱）；
> 用 **openai** 则走真实模型（deepseek-v4-flash，按 token 计费），边界行为可能略有差异。
> 全部断言都是「系统应该怎么做」，测的是系统拦截/兜底能力。

## 文件地址（上传用）

| 文件 | 绝对路径 |
|---|---|
| 华东优包报价单.xlsx | `D:\个人通用agentharness\output\procurement-demo-v3\华东优包报价单.xlsx` |
| 沪上包装报价单.pdf | `D:\个人通用agentharness\output\procurement-demo-v3\沪上包装报价单.pdf` |
| 星河包装报价单.pdf | `D:\个人通用agentharness\output\procurement-demo-v3\星河包装报价单.pdf` |
| 错误物料低价样本报价单.xlsx（对抗样本） | `D:\个人通用agentharness\output\procurement-demo-v3\错误物料低价样本报价单.xlsx` |

重新生成这些演示文件：

```powershell
uv run python scripts/generate_procurement_demo.py --output output/procurement-demo-v3
```

## A. 正常路径（应该全部走通）

**A1 完整采购（三份报价）**
```
采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、厚度公差3微米。请比较附件报价并推荐供应商。
```
预期：capture → 分析 → 「星河包装报价单.pdf」供应商名低置信度 → `require_human` → 在报价复核面板把供应商名改为「星河包装」→ 开始比价 → 生成快照 → 正式选定「华东优包」→ 审批 → 报告 `passed / verified: true`。

**A2 缺公差（用默认值，不追问）**
```
采购5000个PE快递袋，规格250x350mm，厚60微米，10天内交付，需开票。
```
预期：系统明确告知使用默认公差（尺寸 2mm / 厚度 3μm），不会因缺公差而卡住。

**A3 外币汇率**
```
采购2000个PE快递袋，规格250x350mm，厚60微米，单色印刷，15天内交付，需开票；USD/CNY按7.2，EUR/CNY按8.0。
```
预期：汇率表含 USD/EUR，报价按汇率折算到本位币 CNY，金额正确。

## B. 故意写错的输入（系统应该拦住/兜底）

**B1 数量 0**
```
采购0个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，15天内交付上海松江，必须开票；USD/CNY按7.2。
```
预期（fake 模式稳定复现）：`procurement_capture_requirement` 被确定性校验拒绝，任务停在 `require_human` 并提示数量必须 ≥ 1；**不会**拿 0 数量继续比价。

**B2 单价预算低到不合理**
```
采购10000个PE白色快递袋，规格250x350mm、厚60微米，15天内交付，需开票；到货单价预算0.001元。
```
预期：所有报价超预算 → 0 家合格 → 系统不推荐任何供应商（进入人工边界），不会硬选一个。

**B3 规格缺失（不写尺寸/厚度）**
```
采购10000个PE快递袋，15天内交付。
```
预期（fake 模式）：width/height=0 → capture 校验拒绝 → `require_human`，不会用错误规格继续。

**B4 快照失效（先改报价再旧审批）**
正常走到比价快照后 → 人工修正任意一条报价字段 → 再用旧快照点审批。
预期：拒绝并提示「比价快照已变化/失效」，必须重新分析；旧审批不可复用。

**B5 超长审批备注（>500 字符，P0 回归）**
审批时在备注粘贴 600 字中文。
预期：**审批正常通过**。旧代码会 409「采购审批参数不可验证」（该 bug 已修复：审批绑定完整参数 SHA-256）。

**B6 未确认/拒绝审批**
比价完成后审批弹窗不确认（或拒绝）。
预期：不写入任何决定，运行保持 `require_human`，报告不含 `approved`。

## C. 边界文件

- 上传非 XLSX/PDF 或超过 5MB 的文件 → 预期：明确报错「报价文件不得超过 N MB」或解析失败提示，不会崩溃。
- 只传 1 份报价（接口要求至少 2 份）→ 预期：被拒绝。

## 看证据的地方

- 运行报告：任务详情 → 报告（含 Checkpoint、Verification、Approval、Token、证据 SHA-256）。
- 事件流：报告/审计视图可按时间看 `approval_requested`、`verification_result`、`run_status`。
- 数据目录：`output/dev-run`（gitignore，不影响仓库）。
