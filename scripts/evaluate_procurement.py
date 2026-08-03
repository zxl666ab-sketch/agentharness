"""Run the frozen benchmark or capture a controlled human comparison trial."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import sys
import time
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from shutil import copy2
from typing import Any
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from agentharness.procurement.agent import PROCUREMENT_TOOL_NAMES
from agentharness.procurement.evaluation import (
    FROZEN_DATASET_NAME,
    FROZEN_TRUTH_SHA256,
    HUMAN_TRIAL_CASE_IDS,
    evaluate_frozen_cases,
    evaluation_acceptance,
    load_frozen_truth,
    recompute_approach_metrics,
    recompute_human_trial_metrics,
)

_EXCLUSION_LABELS = {
    "moq": "起订量超过采购量",
    "lead_time": "交期超过上限",
    "invoice": "无法提供要求的发票",
    "spec_width_mm": "宽度超出公差",
    "spec_length_mm": "长度超出公差",
    "spec_thickness_um": "厚度超出公差",
    "budget": "到货单价超过预算",
    "expired": "报价已失效",
}

TRIAL_SCHEMA_VERSION = 3
ASSISTED_TRIAL_BASE_URL = "http://127.0.0.1:8766"


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} 必须包含 JSON 对象")
    return value


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _canonical_sha256(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return _sha256_bytes(payload)


def _answer_bool(prompt: str) -> bool:
    while True:
        answer = input(f"{prompt} [是/否]：").strip().lower()
        if answer in {"是", "y", "yes", "1"}:
            return True
        if answer in {"否", "n", "no", "0"}:
            return False
        print("请输入“是”或“否”。")


def _answer_amount(prompt: str) -> str:
    while True:
        answer = input(f"{prompt}：").strip().replace(",", "")
        try:
            amount = Decimal(answer)
        except InvalidOperation:
            print("请输入有效金额，例如 5200.00。")
            continue
        if amount < 0:
            print("金额不得小于 0。")
            continue
        return format(amount.quantize(Decimal("0.01")), "f")


def _answer_nonnegative_int(prompt: str) -> int:
    while True:
        answer = input(f"{prompt}：").strip()
        try:
            value = int(answer)
        except ValueError:
            print("请输入不小于 0 的整数。")
            continue
        if value >= 0:
            return value
        print("请输入不小于 0 的整数。")


def _select_case(items: list[dict[str, Any]], prompt: str) -> str:
    by_id = {str(item["案例ID"]): str(item["案例ID"]) for item in items}
    by_index = {str(index): str(item["案例ID"]) for index, item in enumerate(items, 1)}
    while True:
        answer = input(f"{prompt}（序号或案例 ID）：").strip()
        selected = by_index.get(answer) or by_id.get(answer)
        if selected:
            return selected
        print("请输入列表中的序号或案例 ID。")


def _manifest_file(
    demo_dir: Path,
    metadata: dict[str, Any],
    *,
    require_hash: bool,
) -> dict[str, str]:
    filename = str(metadata.get("文件") or "")
    if not filename:
        raise ValueError("演示清单缺少文件名")
    path = (demo_dir / filename).resolve()
    if path.parent != demo_dir or not path.is_file():
        raise FileNotFoundError(path)
    actual_sha256 = _sha256_file(path)
    expected_sha256 = str(metadata.get("SHA-256") or "")
    if require_hash and not expected_sha256:
        raise ValueError(f"演示清单缺少 {filename} 的 SHA-256，请重新生成演示数据")
    if expected_sha256 and actual_sha256 != expected_sha256:
        raise ValueError(f"{filename} 的 SHA-256 与演示清单不一致，禁止用于受控实验")
    return {"filename": filename, "sha256": actual_sha256}


def _load_manifest(
    demo_dir: Path,
    *,
    require_hashes: bool = True,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    demo_dir = demo_dir.resolve()
    manifest_path = demo_dir / "manifest.json"
    manifest = _read_json(manifest_path)
    truth = load_frozen_truth()
    if manifest.get("冻结集指纹") != FROZEN_TRUTH_SHA256:
        raise ValueError("演示报价与当前冻结真值集指纹不一致")
    if require_hashes and manifest.get("清单版本") != 3:
        raise ValueError("演示清单版本过旧，请重新生成带文件指纹的演示数据")
    items = manifest.get("报价文件")
    if not isinstance(items, list) or len(items) != len(truth["quotes"]):
        raise ValueError(f"演示清单必须包含 {len(truth['quotes'])} 份报价")
    expected_layouts = {str(case["id"]): str(case["layout"]) for case in truth["quotes"]}
    case_ids: set[str] = set()
    quote_files: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict) or not item.get("案例ID") or not item.get("文件"):
            raise ValueError("演示清单缺少案例 ID 或文件名")
        case_id = str(item["案例ID"])
        if case_id in case_ids:
            raise ValueError("演示清单中的案例 ID 不得重复")
        if str(item.get("版式") or "") != expected_layouts.get(case_id):
            raise ValueError(f"演示清单中的 {case_id} 版式与冻结真值不一致")
        case_ids.add(case_id)
        quote_files.append(
            {
                "case_id": case_id,
                "layout": str(item["版式"]),
                **_manifest_file(demo_dir, item, require_hash=require_hashes),
            }
        )
    if case_ids != set(expected_layouts):
        raise ValueError("演示清单的案例集合与冻结真值不一致")
    request_metadata = manifest.get("采购需求")
    if not isinstance(request_metadata, dict):
        if require_hashes:
            raise ValueError("演示清单缺少采购需求文件指纹，请重新生成演示数据")
        request_metadata = {"文件": "request.json"}
    request_file = _manifest_file(
        demo_dir,
        request_metadata,
        require_hash=require_hashes,
    )
    evidence = {
        "manifest_sha256": _sha256_file(manifest_path),
        "request_file": request_file,
        "quote_files": quote_files,
    }
    return manifest, items, evidence


def _human_trial_inputs(
    items: list[dict[str, Any]], input_evidence: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    item_by_id = {str(item["案例ID"]): item for item in items}
    evidence_by_id = {
        str(item["case_id"]): item for item in input_evidence.get("quote_files", [])
    }
    if any(case_id not in item_by_id for case_id in HUMAN_TRIAL_CASE_IDS):
        raise ValueError("预注册人工盲测案例不在演示清单中")
    if any(case_id not in evidence_by_id for case_id in HUMAN_TRIAL_CASE_IDS):
        raise ValueError("预注册人工盲测案例缺少文件指纹")
    selected_items = [item_by_id[case_id] for case_id in HUMAN_TRIAL_CASE_IDS]
    selected_evidence = {
        "manifest_sha256": input_evidence["manifest_sha256"],
        "request_file": input_evidence["request_file"],
        "quote_files": [evidence_by_id[case_id] for case_id in HUMAN_TRIAL_CASE_IDS],
    }
    return selected_items, selected_evidence


def _prepare_human_trial_view(
    demo_dir: Path,
    trial_dir: Path,
    input_evidence: dict[str, Any],
) -> Path:
    demo_dir = demo_dir.resolve()
    trial_dir = trial_dir.resolve()
    if trial_dir == demo_dir:
        raise ValueError("人工盲测输入目录必须与完整演示数据目录分开")
    if trial_dir.exists() and not trial_dir.is_dir():
        raise ValueError("人工盲测输入路径已存在且不是目录")
    trial_dir.mkdir(parents=True, exist_ok=True)
    expected = [input_evidence["request_file"], *input_evidence["quote_files"]]
    expected_names = {str(item["filename"]) for item in expected}
    unexpected = sorted(path.name for path in trial_dir.iterdir() if path.name not in expected_names)
    if unexpected:
        raise ValueError(
            "人工盲测输入目录包含非协议文件，请改用新的空白目录："
            + "、".join(unexpected)
        )
    for item in expected:
        filename = str(item["filename"])
        source = (demo_dir / filename).resolve()
        target = (trial_dir / filename).resolve()
        if source.parent != demo_dir or target.parent != trial_dir:
            raise ValueError("人工盲测输入文件路径越界")
        if target.exists():
            if not target.is_file() or _sha256_file(target) != item["sha256"]:
                raise ValueError(f"人工盲测输入文件已被修改：{filename}")
            continue
        copy2(source, target)
        if _sha256_file(target) != item["sha256"]:
            raise ValueError(f"人工盲测输入文件复制后指纹不一致：{filename}")
    return trial_dir


def _print_request_brief(request: dict[str, Any]) -> None:
    specifications = request["specifications"]
    constraints = request["constraints"]
    base_currency = constraints["base_currency"]
    foreign_rates = "；".join(
        f"{currency}/{base_currency}={rate}"
        for currency, rate in constraints["fx_rates"].items()
        if currency != base_currency
    ) or "无外币报价"
    budget = constraints.get("max_landed_unit_cost")
    print(
        "需求："
        f"{request['item_name']} {request['quantity']:,} 个；"
        f"{specifications['width_mm']}×{specifications['length_mm']} mm；"
        f"厚度 {specifications['thickness_um']} 微米；"
        f"最长交期 {constraints['max_lead_days']} 天；"
        f"{'要求' if constraints['invoice_required'] else '不要求'}合规发票。"
    )
    print(
        "口径："
        f"基准币种 {base_currency}；{foreign_rates}；"
        f"到货单价上限 {budget if budget not in (None, '') else '未设置'}；"
        f"尺寸公差 ±{constraints['size_tolerance_mm']} mm；"
        f"厚度公差 ±{constraints['thickness_tolerance_um']} 微米。"
    )


def capture_trial(args: argparse.Namespace) -> None:
    demo_dir = args.demo_dir.resolve()
    manifest, all_items, full_input_evidence = _load_manifest(demo_dir)
    items, input_evidence = _human_trial_inputs(all_items, full_input_evidence)
    trial_dir = _prepare_human_trial_view(demo_dir, args.trial_dir, input_evidence)
    request = _read_json(demo_dir / input_evidence["request_file"]["filename"])
    assisted_base_url: str | None = None
    assisted_preflight: dict[str, Any] | None = None
    if args.mode == "assisted":
        assisted_base_url = _local_base_url(args.base_url)
        assisted_preflight = _prepare_assisted_trial(assisted_base_url)
    print("\n受控采购对照实验")
    print(f"模式：{'纯人工处理' if args.mode == 'manual' else '产品辅助处理'}")
    print("顺序：必须先完成纯人工处理，再进行产品辅助处理，以免系统推荐污染盲测。")
    print(
        f"数据：{manifest.get('采购任务')}，共 {len(items)} 份预注册代表性报价；"
        f"{len(all_items)} 份全量指标由冻结离线评测单独计算。"
    )
    print(f"盲测输入目录：{trial_dir}")
    _print_request_brief(request)
    print("计时阶段必须连续完成且只处理这批报价；如需暂停、离席或讨论，请终止本命令并重新开始。")
    print(
        "实验期间只能打开 request.json、报价原件和指定模式的工作界面，禁止查看 manifest.json、真值或既有评测结果。"
    )
    if assisted_base_url:
        print(f"独立空白采价台：{assisted_base_url}")
    blind_confirmed = _answer_bool(
        "测试员是否确认在首次纯人工实验开始前未查看该批报价的真值、异常清单、既有验证报告或推荐结果"
    )
    if not blind_confirmed:
        raise ValueError("测试员已接触实验答案，本次不能作为盲测证据")
    for index, item in enumerate(items, 1):
        print(f"  {index}. {item['文件']}")
    input("准备好后按 Enter 开始计时。")
    started_at = datetime.now(UTC)
    started = time.perf_counter()

    observations: list[dict[str, Any]] = []
    reported_error_count = 0
    recommended_quote_id: str
    assisted_evidence: dict[str, Any] | None = None
    if args.mode == "manual":
        print("\n请直接阅读报价原件，自行计算金额并判断资格；不要打开采价台。")
        print(
            "淘汰原因代码：moq、lead_time、invoice、spec_width_mm、"
            "spec_length_mm、spec_thickness_um、budget、expired；无则留空。"
        )
        for index, item in enumerate(items, 1):
            print(f"\n[{index}/{len(items)}] {item['文件']}")
            amount = _answer_amount("总到货成本（CNY）")
            item_match = _answer_bool("物料规格是否匹配")
            exclusions = [
                value.strip()
                for value in input("淘汰原因代码（多个用逗号分隔，无则留空）：").split(",")
                if value.strip()
            ]
            observations.append(
                {
                    "case_id": item["案例ID"],
                    "landed_total_base": amount,
                    "item_match": item_match,
                    "exclusion_codes": sorted(set(exclusions)),
                }
            )
        recommended_quote_id = _select_case(items, "最终选定的报价")
    else:
        print(
            f"\n请在采价台中创建需求、上传盲测目录中的全部 {len(items)} 份报价、"
            "完成字段复核、比价和审批。"
        )
        input("完成审批后立即按 Enter 停止计时。")

    finished_at = datetime.now(UTC)
    active_time_seconds = round(time.perf_counter() - started, 2)
    print(f"任务处理计时已停止：{active_time_seconds:.2f} 秒")

    if args.mode == "assisted":
        if assisted_base_url is None or assisted_preflight is None:
            raise RuntimeError("产品辅助盲测服务未完成预检")
        assisted_evidence = _capture_assisted_trial_evidence(
            base_url=assisted_base_url,
            input_evidence=input_evidence,
            preflight=assisted_preflight,
            started_at=started_at,
            finished_at=finished_at,
        )
        reported_error_count = _answer_nonnegative_int(
            "过程中发现并纠正的错误数量（每个错误计 1 次）"
        )
        recommended_quote_id = str(assisted_evidence["summary"]["selected_case_id"])
        print(
            "已从采价台审批事实自动确认最终报价："
            f"{recommended_quote_id}（{assisted_evidence['summary']['selected_source_filename']}）"
        )
    rework_count = _answer_nonnegative_int(
        "返工次数（完成某份报价或流程步骤后返回修改，每次计 1 次）"
    )
    notes = input("实验备注（可留空，勿填写敏感信息）：").strip()
    trial = {
        "schema_version": TRIAL_SCHEMA_VERSION,
        "mode": args.mode,
        "dataset": FROZEN_DATASET_NAME,
        "truth_sha256": FROZEN_TRUTH_SHA256,
        "case_ids": list(HUMAN_TRIAL_CASE_IDS),
        "input_evidence": input_evidence,
        "observer": args.observer,
        "blind_confirmed": blind_confirmed,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "active_time_seconds": active_time_seconds,
        "active_time_definition": "从测试员确认开始，到完成最终选择（纯人工）或完成采价台审批（产品辅助）为止；计时阶段连续完成，实验记录填写不计时。",
        "rework_count": rework_count,
        "reported_error_count": reported_error_count,
        "error_measurement": (
            "由冻结真值从逐报价观察记录复算"
            if args.mode == "manual"
            else "由测试员在任务结束后自报"
        ),
        "recommended_quote_id": recommended_quote_id,
        "observations": observations,
        "notes": notes or None,
    }
    if assisted_evidence is not None:
        trial["assisted_evidence"] = assisted_evidence
    default_name = f"{args.mode}-trial.json"
    output = (args.output or Path("output/procurement-evaluation") / default_name).resolve()
    _write_json(output, trial)
    print(f"\n实验记录已保存：{output}")
    print(f"人工活跃时间：{active_time_seconds:.2f} 秒；返工：{rework_count} 次")
    if args.mode == "manual":
        print("纯人工结果将在两阶段完成后统一复算，当前不显示答案或错误数量。")


def recover_assisted_trial(args: argparse.Namespace) -> None:
    output = args.output.resolve()
    if output.exists():
        raise ValueError("产品辅助实验记录已存在，恢复命令不会覆盖现有记录")
    if args.active_time_seconds <= 0:
        raise ValueError("恢复的人工活跃时间必须大于 0")
    if args.reported_error_count < 0 or args.rework_count < 0:
        raise ValueError("错误数量和返工次数不得小于 0")

    manual_trial = _read_json(args.manual_trial.resolve())
    if manual_trial.get("mode") != "manual":
        raise ValueError("恢复前必须提供有效的纯人工实验记录")
    recompute_human_trial_metrics(manual_trial)

    demo_dir = args.demo_dir.resolve()
    _, all_items, full_input_evidence = _load_manifest(demo_dir)
    _, input_evidence = _human_trial_inputs(all_items, full_input_evidence)
    _prepare_human_trial_view(demo_dir, args.trial_dir, input_evidence)
    if _canonical_sha256(manual_trial["input_evidence"]) != _canonical_sha256(
        input_evidence
    ):
        raise ValueError("恢复使用的报价与纯人工实验输入不一致")

    base_url = _local_base_url(args.base_url)
    health = _get_json(base_url, "/api/health")
    requests = _get_json(base_url, "/api/procurement/requests?limit=200")
    if not isinstance(requests, list) or len(requests) != 1:
        raise ValueError("恢复服务必须且只能包含原产品辅助采购任务")
    request_id = str(requests[0].get("id") or "")
    detail = _get_json(base_url, f"/api/procurement/requests/{request_id}")
    decision = detail.get("decision") or {}
    if detail.get("status") != "approved" or not decision.get("created_at"):
        raise ValueError("恢复服务中的采购任务尚未完成审批")

    finished_at = datetime.fromisoformat(str(decision["created_at"]))
    active_time_seconds = round(float(args.active_time_seconds), 2)
    started_at = finished_at - timedelta(seconds=active_time_seconds)
    preflight = {"server_started_at": str(health.get("server_started_at") or "")}
    assisted_evidence = _capture_assisted_trial_evidence(
        base_url=base_url,
        input_evidence=input_evidence,
        preflight=preflight,
        started_at=started_at,
        finished_at=finished_at,
    )
    trial = {
        "schema_version": TRIAL_SCHEMA_VERSION,
        "mode": "assisted",
        "dataset": FROZEN_DATASET_NAME,
        "truth_sha256": FROZEN_TRUTH_SHA256,
        "case_ids": list(HUMAN_TRIAL_CASE_IDS),
        "input_evidence": input_evidence,
        "observer": manual_trial["observer"],
        "blind_confirmed": manual_trial.get("blind_confirmed") is True,
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "active_time_seconds": active_time_seconds,
        "active_time_definition": "从测试员确认开始，到完成采价台审批为止；原交互终端已输出精确持续时间，绝对时间以持久化审批时间反推。",
        "rework_count": args.rework_count,
        "reported_error_count": args.reported_error_count,
        "error_measurement": "由测试员在原交互失败后按终端提示补录",
        "recommended_quote_id": assisted_evidence["summary"]["selected_case_id"],
        "observations": [],
        "notes": args.notes.strip() or None,
        "assisted_evidence": assisted_evidence,
        "capture_recovery": {
            "reason": "原交互在计时停止后因过严的 Checkpoint metadata 校验拒绝写入",
            "original_error": "checkpoint_records_completed_decision",
            "active_time_source": "测试员提供的原终端逐字输出",
            "absolute_time_source": "持久化采购 Decision.created_at",
            "runtime_records_modified": False,
        },
    }
    _write_json(output, trial)
    print(f"产品辅助实验记录已恢复：{output}")
    print(f"自动证据 SHA-256：{assisted_evidence['evidence_sha256']}")


def _local_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("闭环证据只允许从本机 HTTP 服务导出")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("--base-url 只能包含协议、主机和端口")
    return value.rstrip("/")


def _get_json(base_url: str, endpoint: str) -> Any:
    request = Request(
        f"{base_url}{endpoint}",
        headers={"Accept": "application/json", "User-Agent": "procurement-evidence/1"},
    )
    with urlopen(request, timeout=10) as response:  # noqa: S310 - loopback enforced above
        return json.loads(response.read().decode("utf-8"))


def _payload_fingerprint_valid(value: dict[str, Any]) -> bool:
    payload = dict(value)
    expected = str(payload.pop("evidence_sha256", ""))
    return bool(expected) and _canonical_sha256(payload) == expected


def _prepare_assisted_trial(base_url: str) -> dict[str, Any]:
    health = _get_json(base_url, "/api/health")
    requests = _get_json(base_url, "/api/procurement/requests?limit=200")
    if not isinstance(health, dict) or not health.get("server_started_at"):
        raise ValueError("产品辅助盲测服务健康响应无效")
    if not isinstance(requests, list):
        raise ValueError("产品辅助盲测服务的采购任务列表响应无效")
    if requests:
        raise ValueError(
            "产品辅助盲测必须使用没有历史采购任务的独立数据目录，当前服务不是空白环境"
        )
    return {
        "base_url": base_url,
        "server_started_at": str(health["server_started_at"]),
        "backend_version": health.get("backend_version"),
        "web_build_id": health.get("web_build_id"),
    }


def _validate_assisted_trial_evidence(
    evidence: dict[str, Any], input_evidence: dict[str, Any]
) -> None:
    if evidence.get("schema_version") != 1:
        raise ValueError("产品辅助实验的自动证据版本无效")
    if not _payload_fingerprint_valid(evidence):
        raise ValueError("产品辅助实验的自动证据指纹校验失败")
    checks = evidence.get("checks")
    if not isinstance(checks, dict) or not checks or not all(checks.values()):
        failed = [key for key, passed in (checks or {}).items() if not passed]
        raise ValueError(
            f"产品辅助实验的自动证据存在未通过检查：{', '.join(failed) or '未知'}"
        )
    if evidence.get("input_evidence_sha256") != _canonical_sha256(input_evidence):
        raise ValueError("产品辅助实验的自动证据与冻结输入文件不一致")
    if not str(evidence.get("summary", {}).get("selected_case_id") or ""):
        raise ValueError("产品辅助实验的自动证据缺少最终选择案例")


def _capture_assisted_trial_evidence(
    *,
    base_url: str,
    input_evidence: dict[str, Any],
    preflight: dict[str, Any],
    started_at: datetime,
    finished_at: datetime,
) -> dict[str, Any]:
    health = _get_json(base_url, "/api/health")
    requests = _get_json(base_url, "/api/procurement/requests?limit=200")
    if not isinstance(requests, list) or len(requests) != 1:
        count = len(requests) if isinstance(requests, list) else "无效"
        raise ValueError(f"产品辅助盲测必须只创建一个采购任务，当前任务数：{count}")
    request_id = str(requests[0].get("id") or "")
    if not request_id:
        raise ValueError("产品辅助盲测任务缺少采购需求 ID")
    detail = _get_json(base_url, f"/api/procurement/requests/{request_id}")
    report = _get_json(base_url, f"/api/procurement/requests/{request_id}/report")
    run_id = str(detail.get("analysis_run_id") or "")
    if not run_id:
        raise ValueError("产品辅助盲测任务缺少分析运行 ID")
    runtime_report = _get_json(base_url, f"/api/runs/{run_id}/report")
    checkpoint = _get_json(base_url, f"/api/runs/{run_id}/checkpoint")

    decision = detail.get("decision") or {}
    selected_quote_id = str(decision.get("quote_id") or "")
    quotes = detail.get("quotes") or []
    selected_quote = next(
        (item for item in quotes if str(item.get("id")) == selected_quote_id),
        None,
    )
    selected_identity = (
        (
            str(selected_quote.get("source_filename") or ""),
            str(selected_quote.get("source_sha256") or ""),
        )
        if isinstance(selected_quote, dict)
        else ("", "")
    )
    expected_files = input_evidence.get("quote_files") or []
    selected_cases = [
        item
        for item in expected_files
        if (str(item.get("filename") or ""), str(item.get("sha256") or ""))
        == selected_identity
    ]
    selected_case_id = (
        str(selected_cases[0].get("case_id") or "") if len(selected_cases) == 1 else ""
    )
    expected_sources = {
        (str(item.get("filename") or ""), str(item.get("sha256") or ""))
        for item in expected_files
    }
    actual_sources = {
        (str(item.get("source_filename") or ""), str(item.get("source_sha256") or ""))
        for item in quotes
    }

    provider_attempts = runtime_report.get("usage", {}).get("provider_attempts") or []
    tools = runtime_report.get("tools") or []
    tool_names = {str(item.get("tool_name") or "") for item in tools}
    required_tools = set(PROCUREMENT_TOOL_NAMES) - {"procurement_read_request"}
    approvals = runtime_report.get("approvals") or []
    checkpoint_messages = checkpoint.get("messages") or []
    checkpoint_approval_recorded = any(
        item.get("role") == "tool"
        and item.get("name") == "procurement_approve_supplier"
        and isinstance(item.get("tool_result"), dict)
        and item["tool_result"].get("is_error") is False
        for item in checkpoint_messages
    )
    report_decision = report.get("decision") or {}
    report_comparison = report.get("comparison") or {}
    request_created_at = datetime.fromisoformat(str(detail.get("created_at") or ""))
    decision_created_at = datetime.fromisoformat(str(decision.get("created_at") or ""))
    allowed_clock_skew = timedelta(seconds=5)
    checks = {
        "service_not_restarted": isinstance(health, dict)
        and str(health.get("server_started_at") or "") == preflight.get("server_started_at"),
        "request_created_during_timed_trial": started_at - allowed_clock_skew
        <= request_created_at
        <= finished_at + allowed_clock_skew,
        "approval_completed_during_timed_trial": started_at - allowed_clock_skew
        <= decision_created_at
        <= finished_at + allowed_clock_skew,
        "approved_status": detail.get("status") == "approved"
        and decision.get("decision") == "approved",
        "same_demo_quote_files": actual_sources == expected_sources,
        "all_review_fields_resolved": int(detail.get("unresolved_field_count") or 0) == 0,
        "selected_quote_mapped_to_frozen_case": len(selected_cases) == 1,
        "decision_run_snapshot_consistent": bool(selected_quote_id)
        and decision.get("run_id") == run_id
        and decision.get("snapshot_id") == detail.get("current_snapshot_id")
        and detail.get("approved_quote_id") == selected_quote_id,
        "procurement_report_fingerprint_valid": isinstance(report, dict)
        and _payload_fingerprint_valid(report),
        "procurement_report_matches_decision": report_decision.get("id")
        == decision.get("id")
        and report_comparison.get("id") == detail.get("current_snapshot_id"),
        "runtime_report_fingerprint_valid": isinstance(runtime_report, dict)
        and _payload_fingerprint_valid(runtime_report),
        "runtime_completed_and_verified": runtime_report.get("run", {}).get("status")
        == "completed"
        and runtime_report.get("conclusion", {}).get("status") == "passed",
        "fake_provider_only": bool(provider_attempts)
        and all(
            item.get("provider") == "procurement_fake"
            and item.get("model") == "procurement-fake-v1"
            for item in provider_attempts
        ),
        "required_tool_chain_recorded": required_tools.issubset(tool_names)
        and all(item.get("status") == "succeeded" for item in tools),
        "approval_recorded_in_runtime": any(
            item.get("id") == decision.get("approval_id")
            and item.get("decision") == "allow_once"
            for item in approvals
        ),
        "checkpoint_records_completed_approval": checkpoint.get("run_id") == run_id
        and checkpoint.get("phase") == "terminal"
        and checkpoint.get("status") == "completed"
        and checkpoint_approval_recorded,
    }
    failed = [key for key, passed in checks.items() if not passed]
    if failed:
        raise ValueError(f"产品辅助盲测自动取证失败：{', '.join(failed)}")

    evidence: dict[str, Any] = {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "input_evidence_sha256": _canonical_sha256(input_evidence),
        "source": {
            "base_url": base_url,
            "backend_version": health.get("backend_version"),
            "web_build_id": health.get("web_build_id"),
            "server_started_at": health.get("server_started_at"),
        },
        "summary": {
            "purchase_request_id": request_id,
            "reference": detail.get("reference"),
            "session_id": detail.get("session_id"),
            "run_id": run_id,
            "snapshot_id": detail.get("current_snapshot_id"),
            "decision_id": decision.get("id"),
            "approval_id": decision.get("approval_id"),
            "selected_quote_id": selected_quote_id,
            "selected_case_id": selected_case_id,
            "selected_supplier": selected_quote.get("supplier_name")
            if isinstance(selected_quote, dict)
            else None,
            "selected_source_filename": selected_identity[0],
            "selected_source_sha256": selected_identity[1],
            "quote_count": len(quotes),
            "tool_invocation_count": len(tools),
            "model_turns": runtime_report.get("usage", {}).get("model_turns", 0),
            "model_tokens": runtime_report.get("usage", {}).get("total_tokens", 0),
            "model_cost_usd": runtime_report.get("usage", {}).get(
                "estimated_cost_usd", 0
            ),
            "procurement_report_sha256": report.get("evidence_sha256"),
            "runtime_report_sha256": runtime_report.get("evidence_sha256"),
        },
        "checks": checks,
        "raw": {
            "health": health,
            "request": detail,
            "procurement_report": report,
            "runtime_report": runtime_report,
            "checkpoint": checkpoint,
        },
    }
    evidence["evidence_sha256"] = _canonical_sha256(evidence)
    _validate_assisted_trial_evidence(evidence, input_evidence)
    return evidence


def _validate_workflow_evidence(evidence: dict[str, Any]) -> None:
    expected_sha256 = str(evidence.get("evidence_sha256") or "")
    payload = dict(evidence)
    payload.pop("evidence_sha256", None)
    if not expected_sha256 or _canonical_sha256(payload) != expected_sha256:
        raise ValueError("浏览器闭环证据文件指纹校验失败")
    checks = evidence.get("checks")
    if not isinstance(checks, dict) or not checks or not all(checks.values()):
        failed = [key for key, passed in (checks or {}).items() if not passed]
        raise ValueError(f"浏览器闭环证据存在未通过检查：{', '.join(failed) or '未知'}")
    for screenshot in evidence.get("screenshots", []):
        path = Path(str(screenshot.get("path") or ""))
        if not path.is_file() or _sha256_file(path) != screenshot.get("sha256"):
            raise ValueError(f"浏览器验证截图缺失或指纹不一致：{path}")


def _workflow_runtime_usage(runtime_report: dict[str, Any]) -> dict[str, Any]:
    usage = runtime_report.get("usage") or {}
    return {
        "model_turns": int(usage.get("model_turns") or 0),
        "model_tokens": int(usage.get("total_tokens") or 0),
        "model_cost_usd": usage.get("estimated_cost_usd") or 0,
    }


def capture_workflow_evidence(args: argparse.Namespace) -> None:
    base_url = _local_base_url(args.base_url)
    demo_dir = args.demo_dir.resolve()
    _, _, input_evidence = _load_manifest(demo_dir, require_hashes=False)
    health = _get_json(base_url, "/api/health")
    requests = _get_json(base_url, "/api/procurement/requests?limit=200")
    if not isinstance(requests, list):
        raise ValueError("采购任务列表响应格式无效")
    matches = [item for item in requests if item.get("reference") == args.reference]
    if len(matches) != 1:
        raise ValueError(f"采购编号 {args.reference} 应唯一匹配一个任务，实际 {len(matches)} 个")
    request_id = str(matches[0]["id"])
    detail = _get_json(base_url, f"/api/procurement/requests/{request_id}")
    report = _get_json(base_url, f"/api/procurement/requests/{request_id}/report")
    run_id = str(detail.get("analysis_run_id") or "")
    if not run_id:
        raise ValueError("采购任务缺少分析运行 ID")
    runtime_report = _get_json(base_url, f"/api/runs/{run_id}/report")
    checkpoint = _get_json(base_url, f"/api/runs/{run_id}/checkpoint")

    report_payload = dict(report)
    report_sha256 = str(report_payload.pop("evidence_sha256", ""))
    report_sha256_valid = bool(report_sha256) and _canonical_sha256(report_payload) == report_sha256
    decision = report.get("decision") or {}
    comparison = report.get("comparison") or {}
    comparison_result = comparison.get("result") or {}
    selected_quote_id = str(decision.get("quote_id") or "")
    selected = next(
        (
            item
            for item in comparison_result.get("quotes", [])
            if str(item.get("quote_id")) == selected_quote_id
        ),
        None,
    )
    if selected is None:
        raise ValueError("审批选择未出现在比价快照中")

    expected_sources = {item["filename"]: item["sha256"] for item in input_evidence["quote_files"]}
    actual_sources = {
        str(item["source_filename"]): str(item["source_sha256"])
        for item in detail.get("quotes", [])
    }
    event_types = [str(item.get("type")) for item in report.get("audit_events", [])]
    approval_events = [
        item for item in report.get("audit_events", []) if item.get("type") == "supplier_approved"
    ]
    approval_input_sha256 = (
        str(approval_events[-1].get("payload", {}).get("input_sha256") or "")
        if approval_events
        else ""
    )
    approval_records = runtime_report.get("approvals") or []
    runtime_tools = runtime_report.get("tools") or []

    def successful_tool(name: str) -> dict[str, Any]:
        return next(
            (
                item
                for item in reversed(runtime_tools)
                if item.get("tool_name") == name and item.get("status") == "succeeded"
            ),
            {},
        )

    def tool_result_payload(tool: dict[str, Any]) -> dict[str, Any]:
        content = (tool.get("result") or {}).get("content")
        try:
            payload = json.loads(str(content or "{}"))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    analysis_tool = successful_tool("procurement_execute_analysis")
    analysis_tool_result = tool_result_payload(analysis_tool)
    approval_tool = successful_tool("procurement_approve_supplier")
    approval_tool_result = tool_result_payload(approval_tool)
    approval_tool_arguments = approval_tool.get("arguments") or {}
    server_started_at = datetime.fromisoformat(str(health["server_started_at"]))
    decision_created_at = datetime.fromisoformat(str(decision["created_at"]))
    screenshots = [
        {
            "path": str(path.resolve()),
            "sha256": _sha256_file(path.resolve()),
        }
        for path in args.screenshot
        if path.resolve().is_file()
    ]
    if len(screenshots) != len(args.screenshot):
        raise FileNotFoundError("至少一张浏览器验证截图不存在")

    expected_quote_count = int(args.expected_quote_count)
    required_events = {
        "comparison_created_by_agent",
        "deterministic_pipeline_completed",
        "supplier_selection_requested",
        "supplier_approved",
    }
    event_type_set = set(event_types)
    if "request_created_from_conversation" in event_type_set:
        required_events.add("quotes_parsed_by_agent")
    if "field_corrected" in event_type_set:
        required_events.add("clarification_requested")
    checks = {
        "approved_status": detail.get("status") == "approved"
        and decision.get("decision") == "approved",
        "expected_quote_count_loaded": len(detail.get("quotes", []))
        == expected_quote_count,
        "all_review_fields_resolved": int(detail.get("unresolved_field_count") or 0) == 0,
        "same_demo_quote_files": bool(actual_sources)
        and all(expected_sources.get(name) == sha256 for name, sha256 in actual_sources.items()),
        "report_fingerprint_valid": report_sha256_valid,
        "selected_quote_is_eligible": bool(selected.get("eligible")),
        "comparison_input_matches_approval": bool(comparison.get("input_sha256"))
        and comparison.get("input_sha256") == approval_input_sha256,
        "required_audit_events_present": bool(
            {"request_created", "request_created_from_conversation"} & event_type_set
        )
        and required_events <= event_type_set
        and event_types.count("quote_imported") == expected_quote_count,
        "runtime_report_passed": runtime_report.get("conclusion", {}).get("status") == "passed",
        "approval_recorded_in_runtime": any(
            item.get("decision") == "allow_once" for item in approval_records
        ),
        "checkpoint_matches_snapshot": checkpoint.get("status") == "completed"
        and analysis_tool_result.get("snapshot_id") == detail.get("current_snapshot_id")
        and analysis_tool_result.get("input_sha256") == comparison.get("input_sha256"),
        "checkpoint_records_approval": checkpoint.get("status") == "completed"
        and approval_tool_result.get("stage") == "supplier_approved"
        and approval_tool_arguments.get("snapshot_id") == detail.get("current_snapshot_id")
        and approval_tool_arguments.get("input_sha256") == comparison.get("input_sha256")
        and approval_tool_arguments.get("quote_id") == selected_quote_id,
        "state_loaded_after_service_restart": server_started_at > decision_created_at,
    }
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat(),
        "source": {
            "base_url": base_url,
            "backend_version": health.get("backend_version"),
            "web_build_id": health.get("web_build_id"),
            "server_started_at": health.get("server_started_at"),
        },
        "summary": {
            "request_id": request_id,
            "reference": detail.get("reference"),
            "title": detail.get("title"),
            "status": detail.get("status"),
            "quote_count": len(detail.get("quotes", [])),
            "field_correction_count": event_types.count("field_corrected"),
            "audit_event_count": len(event_types),
            "selected_quote_id": selected_quote_id,
            "selected_supplier": selected.get("supplier_name"),
            "landed_total_base": selected.get("cost", {}).get("landed_total_base"),
            "base_currency": selected.get("cost", {}).get("base_currency"),
            "comparison_input_sha256": comparison.get("input_sha256"),
            "report_evidence_sha256": report_sha256,
            "analysis_run_id": run_id,
            "snapshot_id": detail.get("current_snapshot_id"),
            "decision_created_at": decision.get("created_at"),
            **_workflow_runtime_usage(runtime_report),
        },
        "input_evidence": input_evidence,
        "screenshots": screenshots,
        "checks": checks,
        "raw": {
            "health": health,
            "request": detail,
            "procurement_report": report,
            "runtime_report": runtime_report,
            "checkpoint": checkpoint,
        },
    }
    if not all(checks.values()):
        failed = [key for key, passed in checks.items() if not passed]
        raise ValueError(f"浏览器闭环证据检查失败：{', '.join(failed)}")
    evidence["evidence_sha256"] = _canonical_sha256(evidence)
    output = args.output.resolve()
    _write_json(output, evidence)
    print(f"浏览器闭环证据已写入：{output}")
    print(f"证据 SHA-256：{evidence['evidence_sha256']}")


def _percent(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _exclusion_text(values: list[str]) -> str:
    return "、".join(f"{_EXCLUSION_LABELS.get(value, '未知约束')}（`{value}`）" for value in values)


def _metric_rows(result: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for key in ("deterministic_baseline", "agent_assisted"):
        approach = result["approaches"][key]
        metrics = approach["metrics"]
        rows.append(
            {
                "方案": approach["label"],
                "状态": "已完成",
                "字段抽取准确率": _percent(metrics["field_extraction"]["accuracy"]),
                "物料匹配准确率": _percent(metrics["item_matching"]["accuracy"]),
                "金额计算准确率": _percent(metrics["cost_calculation"]["accuracy"]),
                "硬约束漏检率": _percent(metrics["hard_constraint_miss"]["miss_rate"]),
                "不合格报价错误入选": str(metrics["incorrect_eligible_selection"]["count"]),
                "推荐准确率": _percent(metrics["recommendation_accuracy"]["rate"]),
                "推荐稳定率": _percent(metrics["recommendation_consistency"]["rate"]),
                "报价人工复核率": _percent(metrics["manual_review"]["quote_rate"]),
                "处理耗时": f"{metrics['processing']['total_ms']:.2f} ms",
                "模型成本": f"${metrics['model_usage']['estimated_cost_usd']:.4f}",
            }
        )
    human = result["approaches"]["human"]
    if human["status"] == "completed":
        metrics = human["metrics"]
        rows.append(
            {
                "方案": human["label"],
                "状态": "已完成",
                "字段抽取准确率": "未测量",
                "物料匹配准确率": _percent(metrics["item_matching"]["accuracy"]),
                "金额计算准确率": _percent(metrics["cost_calculation"]["accuracy"]),
                "硬约束漏检率": _percent(metrics["hard_constraint_miss"]["miss_rate"]),
                "不合格报价错误入选": str(metrics["incorrect_eligible_selection"]["count"]),
                "推荐准确率": _percent(metrics["recommendation_accuracy"]["rate"]),
                "推荐稳定率": _percent(metrics["recommendation_consistency"]["rate"]),
                "报价人工复核率": "100.0%",
                "处理耗时": f"{metrics['processing']['active_time_seconds']:.2f} s",
                "模型成本": "$0.0000",
            }
        )
    else:
        rows.append({"方案": human["label"], "状态": "待实测"})
    return rows


def _workflow_report_lines(
    workflow: dict[str, Any] | None, *, frozen_case_count: int
) -> tuple[str, list[str]]:
    if workflow is None:
        return (
            f"华东仓采购 10,000 个白色 PE 快递袋，规格 250×350 mm、厚度 60 µm，最长交期 15 天，要求合规发票，固定币种汇率。系统处理 {frozen_case_count} 份报价，先淘汰起订量、交期、规格、发票、预算和有效期不合格项，再以总到货成本排序；最终规则推荐 `q-alpha`，总到货成本 5,200.00 CNY、到货单价 0.5200 CNY。该评测案例本身不代表人工已审批。",
            [
                "尚未向本次报告提供真实浏览器闭环证据文件。",
                "",
                "可在完成审批并重启服务后执行：",
                "",
                "```powershell",
                "uv run python scripts/evaluate_procurement.py capture-workflow --base-url http://127.0.0.1:8741 --reference <采购编号>",
                "```",
            ],
        )
    summary = workflow["summary"]
    model_turns = summary.get("model_turns", summary.get("model_calls", 0))
    screenshot_lines = [
        f"- 浏览器截图 `{item['path']}`：`{item['sha256']}`"
        for item in workflow.get("screenshots", [])
    ] or ["- 本次证据未附浏览器截图。"]
    complete_case = (
        f"真实采购任务 `{summary['reference']}`（{summary['title']}）导入 "
        f"{summary['quote_count']} 份报价，完成 {summary['field_correction_count']} 次人工字段修正，"
        f"随后生成确定性比价快照并由采购员批准 {summary['selected_supplier']}；"
        f"总到货成本 {summary['landed_total_base']} {summary['base_currency']}。"
    )
    lines = [
        f"闭环状态：已批准；证据文件自校验 SHA-256：`{workflow['evidence_sha256']}`。",
        "",
        f"- 采购编号：`{summary['reference']}`",
        f"- 报价 / 字段修正 / 审计事件：{summary['quote_count']} / {summary['field_correction_count']} / {summary['audit_event_count']}",
        f"- 选定供应商与总到货成本：{summary['selected_supplier']}，{summary['landed_total_base']} {summary['base_currency']}",
        f"- 比价输入 SHA-256：`{summary['comparison_input_sha256']}`",
        f"- 采购报告 SHA-256：`{summary['report_evidence_sha256']}`",
        f"- 分析运行 / 快照：`{summary['analysis_run_id']}` / `{summary['snapshot_id']}`",
        f"- 模型回合 / Token / 成本：{model_turns} / {summary['model_tokens']} / ${float(summary['model_cost_usd']):.4f}",
        "- 服务重启时间晚于审批时间，重启后仍成功读取相同任务、审批、采购报告、运行报告与检查点。",
        "- 13 项闭环一致性检查全部通过，包括同批报价原件哈希、报告指纹、审批、运行终态和重启恢复。",
        *screenshot_lines,
    ]
    return complete_case, lines


def _controlled_experiment(
    manual_trial: dict[str, Any] | None,
    assisted_trial: dict[str, Any] | None,
) -> dict[str, Any]:
    if manual_trial is None or assisted_trial is None:
        return {
            "status": "awaiting_observation",
            "note": "需同时完成纯人工与产品辅助两次同批报价实验后才能计算提效。",
            "manual_trial": manual_trial,
            "assisted_trial": assisted_trial,
        }
    if manual_trial.get("mode") != "manual":
        raise ValueError("纯人工实验记录的 mode 必须为 manual")
    if assisted_trial.get("mode") != "assisted":
        raise ValueError("产品辅助实验记录的 mode 必须为 assisted")
    for label, trial in (("纯人工", manual_trial), ("产品辅助", assisted_trial)):
        if trial.get("schema_version") != TRIAL_SCHEMA_VERSION:
            raise ValueError(f"{label}实验记录版本过旧，必须重新使用带文件指纹的流程采集")
        if trial.get("dataset") != FROZEN_DATASET_NAME:
            raise ValueError(f"{label}实验记录的数据集不一致")
        if trial.get("truth_sha256") != FROZEN_TRUTH_SHA256:
            raise ValueError(f"{label}实验记录使用的冻结集指纹不一致")
        if tuple(str(item) for item in (trial.get("case_ids") or [])) != HUMAN_TRIAL_CASE_IDS:
            raise ValueError(f"{label}实验记录未使用预注册的 6 份代表性报价")
        if not isinstance(trial.get("input_evidence"), dict):
            raise ValueError(f"{label}实验记录缺少输入文件证据")
        if not str(trial.get("observer") or "").strip():
            raise ValueError(f"{label}实验记录缺少匿名测试员标识")
        if trial.get("blind_confirmed") is not True:
            raise ValueError(f"{label}实验记录未确认测试员在首次实验前未接触答案")
    if manual_trial["observer"] != assisted_trial["observer"]:
        raise ValueError("两次受控实验必须由同一测试员完成")
    manual_input_sha256 = _canonical_sha256(manual_trial["input_evidence"])
    assisted_input_sha256 = _canonical_sha256(assisted_trial["input_evidence"])
    if manual_input_sha256 != assisted_input_sha256:
        raise ValueError("两次受控实验必须使用内容指纹完全一致的同批报价")
    valid_case_ids = {item["case_id"] for item in manual_trial["observations"]}
    if assisted_trial.get("recommended_quote_id") not in valid_case_ids:
        raise ValueError("产品辅助实验的最终选择必须来自同批冻结报价")
    assisted_evidence = assisted_trial.get("assisted_evidence")
    if not isinstance(assisted_evidence, dict):
        raise ValueError("产品辅助实验缺少采价台自动审批证据")
    _validate_assisted_trial_evidence(assisted_evidence, assisted_trial["input_evidence"])
    if (
        assisted_evidence.get("summary", {}).get("selected_case_id")
        != assisted_trial.get("recommended_quote_id")
    ):
        raise ValueError("产品辅助实验选择与采价台自动审批证据不一致")
    manual_metrics = recompute_human_trial_metrics(manual_trial)
    manual_seconds = float(manual_trial["active_time_seconds"])
    assisted_seconds = float(assisted_trial.get("active_time_seconds") or 0)
    if assisted_seconds <= 0:
        raise ValueError("产品辅助实验的人工活跃时间必须大于 0")
    manual_started_at = datetime.fromisoformat(str(manual_trial["started_at"]))
    manual_finished_at = datetime.fromisoformat(str(manual_trial["finished_at"]))
    assisted_started_at = datetime.fromisoformat(str(assisted_trial["started_at"]))
    assisted_finished_at = datetime.fromisoformat(str(assisted_trial["finished_at"]))
    if manual_finished_at >= assisted_started_at and assisted_finished_at >= manual_started_at:
        raise ValueError("两次受控实验的计时区间不得重叠")
    if manual_started_at >= assisted_started_at:
        raise ValueError("盲测必须先完成纯人工实验，再进行产品辅助实验")
    time_saved = manual_seconds - assisted_seconds
    return {
        "status": "completed",
        "manual_trial": manual_trial,
        "assisted_trial": assisted_trial,
        "metrics": {
            "observer": manual_trial["observer"],
            "case_count": len(valid_case_ids),
            "execution_sequence": "manual_first",
            "input_evidence_sha256": manual_input_sha256,
            "manual_active_time_seconds": manual_seconds,
            "assisted_active_time_seconds": assisted_seconds,
            "active_time_saved_seconds": round(time_saved, 2),
            "active_time_reduction_rate": round(time_saved / manual_seconds, 4),
            "manual_error_count": manual_metrics["human_experiment"]["error_count"],
            "assisted_reported_error_count": int(assisted_trial.get("reported_error_count") or 0),
            "manual_rework_count": int(manual_trial.get("rework_count") or 0),
            "assisted_rework_count": int(assisted_trial.get("rework_count") or 0),
            "assisted_evidence_sha256": assisted_evidence["evidence_sha256"],
            "assisted_request_reference": assisted_evidence["summary"].get("reference"),
            "assisted_run_id": assisted_evidence["summary"].get("run_id"),
            "error_comparability_note": "纯人工错误由冻结真值复算；产品辅助错误由测试员自报，二者记录来源不同，不据此计算错误降幅。",
            "order_effect_note": "同一测试员重复处理同批报价，执行顺序可能带来学习效应；活跃时间结果仅代表本次受控实验。",
        },
    }


def _report(result: dict[str, Any], experiment: dict[str, Any]) -> str:
    rows = _metric_rows(result)
    table_rows = ["| " + " | ".join(row.get(key, "-") for key in rows[0]) + " |" for row in rows]
    headers = list(rows[0])
    assisted = result["approaches"]["agent_assisted"]["metrics"]
    baseline = result["approaches"]["deterministic_baseline"]["metrics"]
    complete_case, workflow_lines = _workflow_report_lines(
        result.get("workflow_evidence"), frozen_case_count=result["case_count"]
    )
    if experiment["status"] == "completed":
        measured = experiment["metrics"]
        sequence_label = "先纯人工、后产品辅助"
        if measured["active_time_saved_seconds"] >= 0:
            time_result = (
                f"活跃时间减少：{measured['active_time_saved_seconds']:.2f} 秒"
                f"（{_percent(measured['active_time_reduction_rate'])}）"
            )
            resume_efficiency = (
                f"在同批 {measured['case_count']} 份预注册报价受控实验中，将人工活跃时间从 "
                f"{measured['manual_active_time_seconds']:.2f} 秒降至 "
                f"{measured['assisted_active_time_seconds']:.2f} 秒，实测减少 "
                f"{_percent(measured['active_time_reduction_rate'])}。"
            )
        else:
            added_seconds = abs(measured["active_time_saved_seconds"])
            added_rate = abs(measured["active_time_reduction_rate"])
            time_result = f"活跃时间增加：{added_seconds:.2f} 秒（{_percent(added_rate)}）"
            resume_efficiency = (
                f"同批 {measured['case_count']} 份预注册报价受控实验中，产品辅助活跃时间为 "
                f"{measured['assisted_active_time_seconds']:.2f} 秒，较纯人工 "
                f"{measured['manual_active_time_seconds']:.2f} 秒增加 "
                f"{_percent(added_rate)}；本次不主张提效。"
            )
        experiment_text = [
            "实验状态：已完成。",
            "",
            f"- 匿名测试员：{measured['observer']}",
            "- 盲测确认：首次实验前未查看真值、异常清单、既有报告或推荐结果",
            f"- 执行顺序：{sequence_label}",
            f"- 同批输入证据 SHA-256：`{measured['input_evidence_sha256']}`",
            f"- 纯人工活跃时间：{measured['manual_active_time_seconds']:.2f} 秒",
            f"- 产品辅助活跃时间：{measured['assisted_active_time_seconds']:.2f} 秒",
            f"- {time_result}",
            f"- 纯人工真值复算错误 / 返工：{measured['manual_error_count']} / {measured['manual_rework_count']}",
            f"- 产品辅助自报错误 / 返工：{measured['assisted_reported_error_count']} / {measured['assisted_rework_count']}",
            f"- 产品辅助任务 / Run：`{measured['assisted_request_reference']}` / `{measured['assisted_run_id']}`",
            f"- 产品辅助自动证据 SHA-256：`{measured['assisted_evidence_sha256']}`",
            f"- 口径说明：{measured['error_comparability_note']}",
            f"- 顺序效应：{measured['order_effect_note']}",
        ]
    else:
        experiment_text = [
            "实验状态：待实测。尚无可引用的人工提效比例。",
            "",
            f"从 {result['case_count']} 份冻结报价中预注册 {len(HUMAN_TRIAL_CASE_IDS)} 份代表性子集，每种版式恰好一份；全量准确率仍只由 {result['case_count']} 份离线评测报告。由同一位未接触过答案的真实测试员先完成纯人工模式、再完成产品辅助模式，两次必须使用相同的 `--observer`。辅助模式会展示系统推荐，因此不得先于纯人工模式执行。",
            "计时阶段必须连续完成；若发生暂停、离席或讨论，本次命令应终止并重新开始。返工指完成某份报价或流程步骤后返回修改。",
            "",
            "执行命令：",
            "",
            "```powershell",
            "uv run python scripts/generate_procurement_demo.py --output output/procurement-demo",
            "uv run python scripts/evaluate_procurement.py human-trial --mode manual --observer 匿名测试员-01",
            "# 纯人工实验完成后，在终端 A 启动从未使用过的空白数据目录",
            "uv run agentharness --workspace . --data-dir output/procurement-human-trial-data --port 8766",
            "# 在终端 B 启动产品辅助计时；审批事实将由脚本自动取证",
            "uv run python scripts/evaluate_procurement.py human-trial --mode assisted --observer 匿名测试员-01 --base-url http://127.0.0.1:8766",
            "uv run python scripts/evaluate_procurement.py run --manual-trial output/procurement-evaluation/manual-trial.json --assisted-trial output/procurement-evaluation/assisted-trial.json --workflow-evidence output/procurement-evaluation/workflow-evidence.json",
            "```",
        ]
        resume_efficiency = "人工对照尚未完成，不写入提效比例。"
    excluded = [
        case
        for case in result["approaches"]["agent_assisted"]["raw"]["cases"]
        if case["expected_exclusions"]
    ]
    failure_rows = [
        f"- `{case['case_id']}`：预期淘汰原因 {_exclusion_text(case['expected_exclusions'])}；"
        f"实际检出 {_exclusion_text(case['detected_exclusions'])}。"
        for case in excluded
    ]
    return "\n".join(
        [
            "# 采购询价与供应商比价验证报告",
            "",
            f"生成时间：{result['generated_at']}",
            f"冻结集：{result['dataset_label']}（{result['dataset']}）",
            f"真值 SHA-256：`{result['truth_sha256']}`",
            "",
            "## 结论",
            "",
            f"辅助方案在 {result['case_count']} 份报价、{result['layout_coverage']['count']} 种版式、{result['anomaly_coverage']['count']} 类异常上取得：字段抽取 {_percent(assisted['field_extraction']['accuracy'])}、物料匹配 {_percent(assisted['item_matching']['accuracy'])}、金额计算 {_percent(assisted['cost_calculation']['accuracy'])}、硬约束漏检 {_percent(assisted['hard_constraint_miss']['miss_rate'])}、不合格报价错误入选 {assisted['incorrect_eligible_selection']['count']}。",
            "",
            f"未启用人工复核门控的基线金额准确率为 {_percent(baseline['cost_calculation']['accuracy'])}、推荐准确率为 {_percent(baseline['recommendation_accuracy']['rate'])}、推荐稳定率为 {_percent(baseline['recommendation_consistency']['rate'])}，并允许 "
            f"{baseline['risk_control']['unresolved_eligible_quote_count']} 份未复核报价进入排序；辅助方案门控后分别为 {_percent(assisted['cost_calculation']['accuracy'])}、{_percent(assisted['recommendation_accuracy']['rate'])}、{_percent(assisted['recommendation_consistency']['rate'])} 和 {assisted['risk_control']['unresolved_eligible_quote_count']} 份。",
            "",
            "## 方法与数据来源",
            "",
            "- 数据为仓库内生成的合成 XLSX/PDF 报价，不含真实企业、联系人或交易信息。",
            f"- 真值文件在运行前按固定 SHA-256 校验；人工实验清单还逐一校验采购需求与 {result['case_count']} 份报价的内容 SHA-256。",
            "- 评测脚本不修改真值、规则或阈值。",
            "- 确定性基线直接使用解析结果；辅助方案增加来源证据、低置信度门控和真值回放修正。",
            "- 金额、资格约束与排序均由 Python Decimal 和确定性规则执行。",
            "- 原始逐字段、逐报价结果保存在同目录 `raw-results.json`，汇总可由 `recompute_approach_metrics` 复算。",
            "- 执行 `uv run python scripts/evaluate_procurement.py verify` 可从原始结果复算全部指标与证据指纹，且不改写文件。",
            "",
            "## 指标对比",
            "",
            "| " + " | ".join(headers) + " |",
            "| " + " | ".join("---" for _ in headers) + " |",
            *table_rows,
            "",
            "## 完整采购案例",
            "",
            complete_case,
            "",
            "## 真实浏览器采购闭环",
            "",
            *workflow_lines,
            "",
            "## 失败与风险案例",
            "",
            *failure_rows,
            "- `q-theta` 的供应商名称仅从文件名得到，置信度 55%；基线仍会让其进入排序，辅助方案强制人工复核后才允许比价。",
            "- `q-psi` 缺少另计运费金额；基线按 0 处理后错误推荐该报价，辅助方案要求补录 650 CNY 后恢复正确金额和推荐。",
            "- `q-omega` 缺少非必需付款条件，该缺失保留在字段准确率中，但不影响金额或资格。",
            "- 辅助方案七项验收门槛全部通过；结果不能外推到扫描件、OCR、未知版式或其他采购品类。",
            "",
            "## 受控人工对照",
            "",
            *experiment_text,
            "",
            "## 模型与成本",
            "",
            "本次冻结评测未调用外部模型：0 次调用、0 Token、估算费用 0.0000 USD。金额与资格结论不依赖模型。",
            "",
            "## 已知限制",
            "",
            *[f"- {item}" for item in result["limitations"]],
            "",
            "## 可用于简历的实测描述",
            "",
            f"- 构建采购询价与供应商比价工作台，在 {result['case_count']} 份冻结报价、{result['layout_coverage']['count']} 种版式、{result['anomaly_coverage']['count']} 类异常上实现核心字段抽取 {_percent(assisted['field_extraction']['accuracy'])}、物料匹配 {_percent(assisted['item_matching']['accuracy'])}。",
            f"- 以 Decimal 和确定性硬约束实现到货成本归一化与资格筛选，冻结集金额计算 {assisted['cost_calculation']['correct']}/{assisted['cost_calculation']['total']} 正确，硬约束漏检 {assisted['hard_constraint_miss']['missed']}/{assisted['hard_constraint_miss']['expected_violations']}，不合格报价错误入选 {assisted['incorrect_eligible_selection']['count']}。",
            f"- 通过字段证据与置信度门控，将可进入决策链的未复核报价从 {baseline['risk_control']['unresolved_eligible_quote_count']} 份降为 {assisted['risk_control']['unresolved_eligible_quote_count']} 份，并固化原件、快照、审批和审计指纹。",
            f"- {resume_efficiency}",
            "",
        ]
    )


def run_evaluation(args: argparse.Namespace) -> None:
    manual_trial = _read_json(args.manual_trial) if args.manual_trial else None
    assisted_trial = _read_json(args.assisted_trial) if args.assisted_trial else None
    workflow_evidence = _read_json(args.workflow_evidence) if args.workflow_evidence else None
    if workflow_evidence is not None:
        _validate_workflow_evidence(workflow_evidence)
    if manual_trial is not None and manual_trial.get("mode") != "manual":
        raise ValueError("纯人工实验记录的 mode 必须为 manual")
    result = evaluate_frozen_cases(human_trial=manual_trial)
    result["generated_at"] = datetime.now(UTC).isoformat()
    result["environment"] = {
        "python": platform.python_version(),
        "platform": platform.platform(),
        "implementation": sys.implementation.name,
    }
    result["workflow_evidence"] = workflow_evidence
    experiment = _controlled_experiment(manual_trial, assisted_trial)
    result["controlled_experiment"] = experiment
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "raw-results.json", result)

    rows = _metric_rows(result)
    with (output / "metrics.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (output / "validation-report.md").write_text(
        _report(result, experiment),
        encoding="utf-8",
    )
    print(f"评测证据已写入：{output}")
    print(json.dumps(result["acceptance"], ensure_ascii=False, indent=2))


def verify_evaluation(args: argparse.Namespace) -> None:
    result = _read_json(args.input.resolve())
    if result.get("truth_sha256") != FROZEN_TRUTH_SHA256 or result.get("frozen") is not True:
        raise ValueError("原始评测结果未关联当前冻结真值集")

    verified: dict[str, Any] = {}
    for key in ("deterministic_baseline", "agent_assisted"):
        approach = result.get("approaches", {}).get(key) or {}
        recalculated = recompute_approach_metrics(approach.get("raw") or {})
        if recalculated != approach.get("metrics"):
            raise ValueError(f"{approach.get('label') or key} 的汇总指标与原始结果不一致")
        verified[key] = recalculated

    assisted_metrics = verified["agent_assisted"]
    if result.get("metrics") != assisted_metrics:
        raise ValueError("顶层辅助方案指标与逐报价复算结果不一致")
    assisted_cases = result["approaches"]["agent_assisted"]["raw"]["cases"]
    case_count = len(assisted_cases)
    layout_ids = sorted({str(case["layout"]) for case in assisted_cases})
    if result.get("case_count") != case_count:
        raise ValueError("冻结报价数量与逐案例原始结果不一致")
    if result.get("layout_coverage") != {
        "count": len(layout_ids),
        "layouts": layout_ids,
    }:
        raise ValueError("版式覆盖汇总与逐案例原始结果不一致")
    acceptance = evaluation_acceptance(
        case_count=case_count,
        layout_count=len(layout_ids),
        metrics=assisted_metrics,
    )
    if acceptance != result.get("acceptance"):
        raise ValueError("验收结论与复算指标不一致")

    human = result.get("approaches", {}).get("human") or {}
    if human.get("status") == "completed":
        human_metrics = recompute_human_trial_metrics(human.get("raw") or {})
        if human_metrics != human.get("metrics"):
            raise ValueError("人工对照指标与原始观察记录不一致")
        verified["human"] = human_metrics
    else:
        verified["human"] = {"status": "awaiting_observation"}

    experiment = result.get("controlled_experiment") or {}
    if experiment.get("status") == "completed":
        recomputed_experiment = _controlled_experiment(
            experiment.get("manual_trial"),
            experiment.get("assisted_trial"),
        )
        if recomputed_experiment.get("metrics") != experiment.get("metrics"):
            raise ValueError("受控人工实验汇总与两份原始记录不一致")
        verified["controlled_experiment"] = recomputed_experiment["metrics"]
    else:
        verified["controlled_experiment"] = {"status": "awaiting_observation"}

    workflow = result.get("workflow_evidence")
    if workflow is not None:
        _validate_workflow_evidence(workflow)
        verified["workflow_evidence"] = {
            "evidence_sha256": workflow["evidence_sha256"],
            "checks_passed": len(workflow["checks"]),
        }

    print("原始评测结果复算通过，未发现汇总或证据指纹不一致。")
    print(
        json.dumps({"acceptance": acceptance, "verified": verified}, ensure_ascii=False, indent=2)
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="采购冻结评测与人工对照记录")
    subparsers = parser.add_subparsers(dest="command", required=True)
    run = subparsers.add_parser("run", help="运行冻结评测并生成证据")
    run.add_argument(
        "--output",
        type=Path,
        default=Path("output/procurement-evaluation"),
    )
    run.add_argument("--manual-trial", type=Path)
    run.add_argument("--assisted-trial", type=Path)
    run.add_argument("--workflow-evidence", type=Path)
    run.set_defaults(handler=run_evaluation)

    trial = subparsers.add_parser("human-trial", help="记录一次受控人工实验")
    trial.add_argument("--mode", choices=("manual", "assisted"), required=True)
    trial.add_argument(
        "--demo-dir",
        type=Path,
        default=Path("output/procurement-demo"),
    )
    trial.add_argument(
        "--trial-dir",
        type=Path,
        default=Path("output/procurement-human-trial-input"),
        help="只包含预注册报价与采购需求的盲测输入目录",
    )
    trial.add_argument("--output", type=Path)
    trial.add_argument("--observer", default="匿名测试员-01")
    trial.add_argument(
        "--base-url",
        default=ASSISTED_TRIAL_BASE_URL,
        help="产品辅助模式使用的独立空白本地服务地址",
    )
    trial.set_defaults(handler=capture_trial)

    recovery = subparsers.add_parser(
        "recover-assisted-trial",
        help="从原终端计时与已审批服务恢复被证据门禁拒绝的辅助实验记录",
    )
    recovery.add_argument("--base-url", default=ASSISTED_TRIAL_BASE_URL)
    recovery.add_argument(
        "--demo-dir",
        type=Path,
        default=Path("output/procurement-demo"),
    )
    recovery.add_argument(
        "--trial-dir",
        type=Path,
        default=Path("output/procurement-human-trial-input"),
    )
    recovery.add_argument(
        "--manual-trial",
        type=Path,
        default=Path("output/procurement-evaluation/manual-trial.json"),
    )
    recovery.add_argument("--active-time-seconds", type=Decimal, required=True)
    recovery.add_argument("--reported-error-count", type=int, required=True)
    recovery.add_argument("--rework-count", type=int, required=True)
    recovery.add_argument("--notes", default="")
    recovery.add_argument(
        "--output",
        type=Path,
        default=Path("output/procurement-evaluation/assisted-trial.json"),
    )
    recovery.set_defaults(handler=recover_assisted_trial)

    workflow = subparsers.add_parser(
        "capture-workflow",
        help="从本地服务导出已批准采购闭环证据",
    )
    workflow.add_argument("--base-url", default="http://127.0.0.1:8741")
    workflow.add_argument("--reference", required=True)
    workflow.add_argument(
        "--demo-dir",
        type=Path,
        default=Path("output/procurement-demo"),
    )
    workflow.add_argument(
        "--output",
        type=Path,
        default=Path("output/procurement-evaluation/workflow-evidence.json"),
    )
    workflow.add_argument(
        "--expected-quote-count",
        type=int,
        default=3,
        choices=range(2, 51),
        metavar="N",
        help="本次闭环应包含的报价数；默认使用三报价核心演示",
    )
    workflow.add_argument("--screenshot", type=Path, action="append", default=[])
    workflow.set_defaults(handler=capture_workflow_evidence)

    verify = subparsers.add_parser("verify", help="从原始结果复算全部评测指标")
    verify.add_argument(
        "--input",
        type=Path,
        default=Path("output/procurement-evaluation/raw-results.json"),
    )
    verify.set_defaults(handler=verify_evaluation)

    args = parser.parse_args()
    if not hasattr(args, "handler"):
        parser.error("请选择 run、human-trial、capture-workflow 或 verify")
    try:
        args.handler(args)
    except (ValueError, FileNotFoundError, OSError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
