"""P2-2 修正回灌：把人工修正记录导出为评测集扩展候选。

从只读接口 GET /api/procurement/corrections 拉取全部人工修正记录，转换为
评测集扩展候选并写入新文件（冻结资源不动，用户审核后才提交启用）。
内容级幂等：同输入实时数据 + 稳定排序 (created_at, id) 下 items 字段一致；
exported_at 每次运行变化，属导出元数据，不参与内容比对。

用法：
  uv run python scripts/export_corrections_to_eval.py [--base-url http://127.0.0.1:8741] [--out procurement-service/src/main/resources/frozen/frozen-evaluation-corrections.json]
"""

from __future__ import annotations

import argparse
import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# 默认输出到 frozen 资源目录（与其它冻结评测文件同处；提交前需人工审核）
DEFAULT_OUT = ROOT / "procurement-service/src/main/resources/frozen/frozen-evaluation-corrections.json"


def fetch_corrections(base_url: str, page_size: int = 100) -> list[dict]:
    items: list[dict] = []
    page = 0
    while True:
        url = f"{base_url}/api/procurement/corrections?page={page}&size={page_size}"
        with urllib.request.urlopen(url, timeout=15) as response:  # noqa: S310 - 本地只读接口
            body = json.loads(response.read().decode("utf-8"))
        page_items = body.get("items") or []
        items.extend(page_items)
        if len(page_items) < page_size or not body.get("total"):
            break
        page += 1
    return items


def build_export(items: list[dict]) -> dict:
    # 幂等：按 (created_at, id) 稳定排序
    ordered = sorted(items, key=lambda item: (str(item.get("created_at") or ""), str(item.get("id") or "")))
    chosen = sum(1 for item in ordered if item.get("chosen_from_conflicts"))
    return {
        "schema_version": 1,
        "dataset": "human-corrections-export",
        "dataset_label": "人工修正记录（修正回灌评测候选，用户审核后启用；synthetic 演示数据）",
        "source": "quote_correction",
        "exported_at": datetime.now(UTC).isoformat(),
        "count": len(ordered),
        "chosen_from_conflicts_count": chosen,
        "items": [
            {
                "id": item.get("id"),
                "task_id": item.get("task_id"),
                "task_reference": item.get("task_reference"),
                "quote_id": item.get("quote_id"),
                "supplier_name": item.get("supplier_name"),
                "field": item.get("field"),
                "old_value": item.get("old_value"),
                "new_value": item.get("new_value"),
                "chosen_from_conflicts": bool(item.get("chosen_from_conflicts")),
                "actor": item.get("actor"),
                "created_at": item.get("created_at"),
            }
            for item in ordered
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export human quote corrections as eval-extension candidates (P2-2)")
    parser.add_argument("--base-url", default="http://127.0.0.1:8741")
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    args = parser.parse_args()

    items = fetch_corrections(args.base_url)
    export = build_export(items)
    out = Path(args.out)
    out.write_text(json.dumps(export, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"导出 {export['count']} 条人工修正（冲突候选单选 {export['chosen_from_conflicts_count']} 条）→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
