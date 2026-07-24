"""Run the two offline acceptance demonstrations against the real Harness entry."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any

from agentharness import Harness, RunRequest
from agentharness.contracts import (
    ApprovalMode,
    VerificationCheck,
    VerificationPolicy,
)
from agentharness.providers.fake import FakeModelAdapter


async def run_demos(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=False)

    context_dir = output_dir / "context"
    workspace_root = context_dir / "workspace"
    cwd = workspace_root / "packages" / "app"
    skill = context_dir / "skills" / "alpha"
    cwd.mkdir(parents=True)
    skill.mkdir(parents=True)
    (workspace_root / "AGENTS.md").write_text(
        "Keep context sources deterministic and never expose credentials.", encoding="utf-8"
    )
    (cwd / "WORKBUDDY.md").write_text(
        "Verify each alpha workspace result.", encoding="utf-8"
    )
    (cwd / "README.md").write_text("alpha workspace evidence\n", encoding="utf-8")
    (skill / "SKILL.md").write_text(
        "---\nname: alpha-context\ndescription: alpha workspace inspection\n---\n"
        "Use the read tool and cite observed evidence.",
        encoding="utf-8",
    )

    context_harness = Harness(data_dir=context_dir / "data")
    context_harness.storage.add_memory(
        "alpha workspace prefers deterministic evidence", source="acceptance-demo"
    )
    context_provider = FakeModelAdapter(
        script=[
            {
                "kind": "tools",
                "tools": [{"name": "read_file", "arguments": {"path": "README.md"}}],
            },
            {"kind": "text", "text": "alpha workspace inspected with evidence"},
        ]
    )
    context_harness.register_provider("context-demo", context_provider)
    try:
        context_result = await context_harness.run(
            RunRequest(
                message="Inspect the alpha workspace with read_file",
                provider="context-demo",
                approval=ApprovalMode.auto,
                cwd=str(cwd),
                extra_dirs=[str(workspace_root)],
                skills_dirs=[str(context_dir / "skills")],
            )
        )
        manifests = context_harness.get_context_manifests(context_result.run_id)
        context_events = context_harness.get_events(run_id=context_result.run_id, limit=1000)
        stable_sections = {
            section: [
                sorted(
                    item["content_hash"]
                    for item in manifest["items"]
                    if item["section"] == section and item["included"]
                )
                for manifest in manifests
            ]
            for section in ("workspace_rules", "skills", "memories")
        }
        context_summary = {
            "run_id": context_result.run_id,
            "status": context_result.status.value,
            "provider_model_turns": len(context_provider.calls),
            "manifest_turns": len(manifests),
            "prefix_fingerprints": [m["prefix_fingerprint"] for m in manifests],
            "budgets_satisfied": [m["total_tokens"] <= m["budget_tokens"] for m in manifests],
            "stable_section_hashes": stable_sections,
            "context_event_count": sum(
                1
                for event in context_events
                if str(event.type.value if hasattr(event.type, "value") else event.type)
                == "context_manifest"
            ),
            "output": context_result.output,
        }
    finally:
        await context_harness.aclose()

    verification_dir = output_dir / "verification"
    verification_workspace = verification_dir / "workspace"
    verification_workspace.mkdir(parents=True)
    verification_harness = Harness(data_dir=verification_dir / "data")
    verification_provider = FakeModelAdapter(
        script=[
            {"kind": "text", "text": "candidate without the required marker"},
            {"kind": "text", "text": "corrected candidate: VERIFIED"},
        ]
    )
    verification_harness.register_provider("verification-demo", verification_provider)
    try:
        verification_result = await verification_harness.run(
            RunRequest(
                message="Return a result containing VERIFIED",
                provider="verification-demo",
                approval=ApprovalMode.auto,
                cwd=str(verification_workspace),
                verification=VerificationPolicy(
                    validators=[
                        VerificationCheck(
                            kind="eval_assert", assertions={"contains": ["VERIFIED"]}
                        )
                    ],
                    max_retries=2,
                ),
            )
        )
        verification_events = verification_harness.get_events(
            run_id=verification_result.run_id, limit=1000
        )
        verification_types = [
            str(event.type.value if hasattr(event.type, "value") else event.type)
            for event in verification_events
        ]
        verification_actions = [
            event.payload.get("action")
            for event in verification_events
            if str(event.type.value if hasattr(event.type, "value") else event.type)
            == "verification_result"
        ]
        verification_summary = {
            "run_id": verification_result.run_id,
            "status": verification_result.status.value,
            "provider_model_turns": len(verification_provider.calls),
            "verification_actions": verification_actions,
            "feedback_events": verification_types.count("verification_feedback"),
            "completed_after_last_verification": (
                verification_types.index("run_completed")
                > max(
                    index
                    for index, value in enumerate(verification_types)
                    if value == "verification_result"
                )
            ),
            "second_turn_received_feedback": any(
                "verification_feedback" in message.content
                for message in verification_provider.calls[1].messages
            ),
            "output": verification_result.output,
        }
    finally:
        await verification_harness.aclose()

    summary = {
        "schema_version": 1,
        "context_control_plane": context_summary,
        "verification_feedback_loop": verification_summary,
    }
    (output_dir / "e2e-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    summary = asyncio.run(run_demos(args.output_dir.expanduser().resolve()))
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
