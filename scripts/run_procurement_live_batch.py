"""Budget-constrained real-model acceptance batch for the procurement agent.

Reproducible per-scenario metrics (turns, tool calls, duplicates, unauthorized
calls, tokens, cost) plus an honest layered comparison against the deterministic
frozen evaluation. Requires a configured live provider (OPENAI_* / procurement
env vars) and pricing so max_cost_usd is enforced.

Usage:
  python scripts/run_procurement_live_batch.py [--scenarios-dir DIR] [--output-dir DIR]
      [--limit N] [--with-approval/--no-approval] [--fake]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agentharness.api.execution import PendingApprovalBroker
from agentharness.contracts import EventType
from agentharness.harness import Harness
from agentharness.procurement.agent import ProcurementAgent
from agentharness.procurement.evaluation import build_case_document, load_frozen_truth
from agentharness.procurement.service import ProcurementService

SCENARIOS_ROOT = Path("output/procurement-scenarios")
DEFAULT_OUTPUT = Path("output/procurement-evaluation")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _scenario_from_dir(scenario_dir: Path) -> dict[str, Any]:
    request = _read_json(scenario_dir / "request.json")
    specs = request["specifications"]
    constraints = request["constraints"]
    fx = constraints.get("fx_rates") or {}
    message = (
        f"采购{request['quantity']}个{specs.get('material', 'PE')}"
        f"{specs.get('color', '白色')}{request['item_name']}，"
        f"规格{specs['width_mm']}x{specs['length_mm']}mm、厚{specs['thickness_um']}微米、"
        f"{specs.get('print_colors', 0)}色印刷，{constraints['max_lead_days']}天内交付"
        f"{constraints.get('destination', '')}，"
        f"{'必须开票' if constraints.get('invoice_required', True) else '无需开票'}；"
        f"USD/CNY按{fx.get('USD', '7.2')}，尺寸公差{constraints.get('size_tolerance_mm', '2')}mm、"
        f"厚度公差{constraints.get('thickness_tolerance_um', '3')}微米。"
    )
    quote_files = sorted(
        path
        for path in scenario_dir.iterdir()
        if path.suffix.lower() in {".xlsx", ".pdf"}
    )
    return {
        "name": scenario_dir.name,
        "source": str(scenario_dir),
        "message": message,
        "attachments": [(path.name, path.read_bytes()) for path in quote_files],
        "expected": _read_json(scenario_dir / "manifest.json").get("预期推荐供应商"),
    }


def _scenario_from_frozen(case_ids: list[str], index: int) -> dict[str, Any]:
    truth = load_frozen_truth()
    by_id = {item["id"]: item for item in truth["quotes"]}
    cases = [by_id[cid] for cid in case_ids]
    return {
        "name": f"frozen-{index + 1:02d}",
        "source": "frozen-eval-dataset",
        "message": (
            "采购10000个PE白色快递袋，规格250x350mm、厚60微米、单色印刷，"
            "15天内交付上海松江，必须开票；USD/CNY按7.2，尺寸公差2mm、"
            "厚度公差3微米。请比较附件报价并推荐供应商。"
        ),
        "attachments": [
            (case["filename"], build_case_document(case)) for case in cases
        ],
        "expected": None,
    }


def _build_scenarios(scenarios_dir: Path, limit: int | None) -> list[dict[str, Any]]:
    scenarios: list[dict[str, Any]] = []
    for scenario_dir in sorted(
        path for path in scenarios_dir.iterdir() if path.is_dir()
    ):
        if (scenario_dir / "request.json").exists():
            scenarios.append(_scenario_from_dir(scenario_dir))
    frozen_pairs = [
        ["q-alpha", "q-beta"],
        ["q-alpha", "q-theta"],
        ["q-gamma", "q-delta"],
        ["q-epsilon", "q-zeta"],
        ["q-eta", "q-iota"],
    ]
    for index, case_ids in enumerate(frozen_pairs):
        try:
            scenarios.append(_scenario_from_frozen(case_ids, index))
        except KeyError:
            continue
    if limit:
        scenarios = scenarios[:limit]
    return scenarios


def _invocation_metrics(harness: Harness, run_id: str) -> dict[str, Any]:
    invocations = harness.storage.list_tool_invocations(run_id)
    events = harness.get_events(run_id=run_id, limit=10_000)
    duplicates = sum(
        1
        for event in events
        if event.type == EventType.tool_call_duplicate
    )
    unauthorized = sum(
        1
        for event in events
        if event.type == EventType.tool_stage_denied
    )
    counts: dict[str, int] = {}
    for invocation in invocations:
        counts[invocation.tool_name] = counts.get(invocation.tool_name, 0) + 1
    return {
        "tool_call_counts": counts,
        "total_tool_calls": len(invocations),
        "duplicate_calls": duplicates,
        "unauthorized_calls": unauthorized,
    }


async def _run_scenario(
    scenario: dict[str, Any],
    *,
    with_approval: bool,
    fake: bool,
) -> dict[str, Any]:
    tmp = tempfile.mkdtemp(prefix="live-batch-")
    root = Path(tmp)
    harness = Harness(data_dir=root / "data")
    broker = PendingApprovalBroker()
    harness.set_approval_callback(broker)
    service = ProcurementService(harness)
    agent = ProcurementAgent(harness, service, approval_broker=broker)
    result: dict[str, Any] = {
        "scenario": scenario["name"],
        "source": scenario["source"],
        "expected": scenario["expected"],
        "tokens": {"input": 0, "output": 0, "total": 0},
        "estimated_cost_usd": None,
    }
    started_at = time.monotonic()
    try:
        accepted = await agent.start(
            message=scenario["message"], attachments=scenario["attachments"]
        )
        run_id = accepted["run_id"]
        result["run_id"] = run_id
        run_result = await agent._tasks[run_id]
        run = harness.get_run(run_id)
        request = service.get_request(accepted["purchase_request_id"])
        comparison = request.get("comparison")
        analysis_ok = run_result.status.value in {"completed", "require_human"} or (
            comparison is not None
        )
        result["status"] = run_result.status.value
        result["error"] = run_result.error
        result["analysis_success"] = bool(analysis_ok)
        result["comparison_produced"] = comparison is not None
        result.update(_invocation_metrics(harness, run_id))
        usage = json.loads(run.get("usage_json") or "{}")
        result["model_turns"] = usage.get("model_turns", 0)
        result["tokens"] = {
            "input": usage.get("input_tokens", 0),
            "output": usage.get("output_tokens", 0),
            "total": usage.get("total_tokens", 0),
        }
        result["estimated_cost_usd"] = usage.get("estimated_cost_usd")
        if with_approval and comparison is not None:
            snapshot = comparison
            try:
                detail = await asyncio.wait_for(
                    agent.approve(
                        accepted["purchase_request_id"],
                        snapshot_id=snapshot["id"],
                        input_sha256=snapshot["input_sha256"],
                        quote_id=snapshot["result"]["recommended_quote_id"],
                        note="跑批自动审批",
                        actor="跑批",
                    ),
                    timeout=180,
                )
                result["approval_status"] = detail.get("status")
                result["approval_success"] = detail.get("status") == "approved"
            except Exception as exc:  # noqa: BLE001
                result["approval_status"] = "failed"
                result["approval_error"] = str(exc)[:500]
                result["approval_success"] = False
        else:
            result["approval_success"] = None
    except Exception as exc:  # noqa: BLE001
        result["status"] = "failed"
        result["error"] = str(exc)[:500]
        result["analysis_success"] = False
        result["approval_success"] = False
    finally:
        result["duration_s"] = round(time.monotonic() - started_at, 3)
        await agent.aclose()
        await harness.aclose()
    return result


async def _main(args: argparse.Namespace) -> int:
    if args.fake:
        import os

        os.environ["AGENTHARNESS_PROCUREMENT_PROVIDER"] = "procurement_fake"
    scenarios = _build_scenarios(args.scenarios_dir, args.limit)
    if not scenarios:
        print("错误：没有可用场景", file=sys.stderr)
        return 2
    print(f"场景数：{len(scenarios)}")
    rows: list[dict[str, Any]] = []
    for index, scenario in enumerate(scenarios, start=1):
        print(f"[{index}/{len(scenarios)}] {scenario['name']} ...", flush=True)
        row = await _run_scenario(
            scenario, with_approval=args.with_approval, fake=args.fake
        )
        rows.append(row)
        print(
            f"   status={row.get('status')} turns={row.get('model_turns')} "
            f"tools={row.get('total_tool_calls')} dup={row.get('duplicate_calls')} "
            f"cost={row.get('estimated_cost_usd')}"
        )

    analysis_ok = [row for row in rows if row.get("analysis_success")]
    approvals_ok = [row for row in rows if row.get("approval_success") is True]
    totals = {
        "tokens": sum(row["tokens"]["total"] for row in rows),
        "cost": sum(
            row.get("estimated_cost_usd") or 0 for row in rows
        ),
    }
    summary = {
        "generated_at": datetime.now(UTC).isoformat(),
        "model": (args.fake and "procurement-fake-v1") or None,
        "provider": "procurement_fake" if args.fake else "openai (live)",
        "scenario_count": len(rows),
        "analysis_success_rate": round(len(analysis_ok) / len(rows), 4),
        "approval_success_rate": (
            round(len(approvals_ok) / len(rows), 4) if args.with_approval else None
        ),
        "avg_model_turns": round(
            sum(row.get("model_turns") or 0 for row in rows) / len(rows), 3
        ),
        "avg_tool_calls": round(
            sum(row.get("total_tool_calls") or 0 for row in rows) / len(rows), 3
        ),
        "total_tokens": totals["tokens"],
        "total_cost_usd": round(totals["cost"], 6),
        "duplicate_calls_total": sum(row.get("duplicate_calls") or 0 for row in rows),
        "unauthorized_calls_total": sum(
            row.get("unauthorized_calls") or 0 for row in rows
        ),
    }

    deterministic: dict[str, Any] = {}
    deterministic_path = DEFAULT_OUTPUT / "raw-results.json"
    if deterministic_path.exists():
        raw = _read_json(deterministic_path)
        metrics = raw.get("metrics") or {}
        deterministic = {
            "dataset": raw.get("dataset"),
            "frozen": raw.get("frozen"),
            "field_extraction": metrics.get("field_extraction"),
            "cost_calculation": metrics.get("cost_calculation"),
            "model_usage": metrics.get("model_usage"),
        }
    payload = {
        "schema_version": 1,
        "summary": summary,
        "scenarios": rows,
        "layering": {
            "note": (
                "确定性冻结评测为 0 模型调用的确定性管线（617/620 字段抽取、"
                "31/31 成本计算）；本批为真实模型编排基线，二者分层呈现、不混用。"
            ),
            "deterministic": deterministic,
            "live_batch": {
                "analysis_success_rate": summary["analysis_success_rate"],
                "avg_model_turns": summary["avg_model_turns"],
                "avg_tool_calls": summary["avg_tool_calls"],
                "total_cost_usd": summary["total_cost_usd"],
            },
        },
    }
    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    json_path = output_dir / f"live-batch-{stamp}.json"
    _write_json(json_path, payload)
    print(f"\n报告：{json_path}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="采购 Agent 真实模型验收跑批")
    parser.add_argument("--scenarios-dir", type=Path, default=SCENARIOS_ROOT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument(
        "--with-approval",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="分析成功后继续审批链路（默认开启）",
    )
    parser.add_argument(
        "--fake", action="store_true", help="使用 procurement_fake 做离线冒烟"
    )
    args = parser.parse_args()
    return asyncio.run(_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
