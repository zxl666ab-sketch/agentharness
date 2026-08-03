"""Generate a Chinese procurement demo without changing the frozen truth set."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from pathlib import Path

from agentharness.procurement.evaluation import (
    FROZEN_TRUTH_SHA256,
    build_case_document,
    load_frozen_truth,
)


def _demo_case(source: dict) -> dict:
    case = deepcopy(source)
    supplier = str(case["demo_supplier_name"])
    suffix = ".xlsx" if case["kind"] == "xlsx" else ".pdf"
    case["filename"] = f"{supplier}报价单{suffix}"
    case["fields"]["supplier_name"] = supplier
    width = case["fields"]["width_mm"]
    length = case["fields"]["length_mm"]
    thickness = case["fields"]["thickness_um"]
    case["fields"]["item_description"] = (
        f"PE 快递袋 {width}×{length} mm，厚度 {thickness} 微米，白色单色印刷"
    )
    return case


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _remove_previous_generation(target: Path) -> None:
    manifest_path = target / "manifest.json"
    if not manifest_path.is_file():
        return
    try:
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return
    owned_names = {
        str(item.get("文件") or "")
        for item in previous.get("报价文件", [])
        if isinstance(item, dict)
    }
    request = previous.get("采购需求")
    if isinstance(request, dict):
        owned_names.add(str(request.get("文件") or ""))
    for name in owned_names:
        path = (target / name).resolve()
        if name and path.parent == target and path.is_file():
            path.unlink()


def generate_demo(target: Path) -> int:
    target = target.resolve()
    target.mkdir(parents=True, exist_ok=True)
    _remove_previous_generation(target)
    truth = load_frozen_truth()
    generated: list[dict[str, object]] = []
    for source in truth["quotes"]:
        case = _demo_case(source)
        document = build_case_document(case, locale="zh-CN")
        (target / case["filename"]).write_bytes(document)
        generated.append(
            {
                "案例ID": case["id"],
                "文件": case["filename"],
                "SHA-256": _sha256(document),
                "供应商": case["fields"]["supplier_name"],
                "版式": case["layout"],
            }
        )
    request = {
        "title": "华东仓快递袋询价",
        "category": "ecommerce_packaging",
        **truth["request"],
    }
    request.pop("id", None)
    request.pop("created_at", None)
    request_bytes = (json.dumps(request, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (target / "request.json").write_bytes(request_bytes)
    (target / "manifest.json").write_text(
        json.dumps(
            {
                "清单版本": 3,
                "说明": "合成演示数据，仅用于本地产品演示，不含真实企业或个人信息。",
                "盲测说明": "本清单只保存输入文件身份与指纹，不包含异常类型、预期淘汰原因、金额真值或推荐结果。",
                "冻结集指纹": FROZEN_TRUTH_SHA256,
                "采购任务": "华东仓快递袋询价",
                "采购需求": {
                    "文件": "request.json",
                    "SHA-256": _sha256(request_bytes),
                },
                "报价文件": generated,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"已在 {target} 生成 {len(generated)} 份中文演示报价")
    return len(generated)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("output/procurement-demo"))
    args = parser.parse_args()
    generate_demo(args.output)


if __name__ == "__main__":
    main()
