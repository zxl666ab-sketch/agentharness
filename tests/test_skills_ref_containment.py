"""SKILL.md refs must not read files outside the skills root.

Skills dirs can point inside an agent-writable workspace. A malicious task can
exactly this), so an unbounded ref turns workspace_write into arbitrary host read.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentharness.tools.skills import _load_skill_body, load_matching_skills

SECRET = "HOST-FILE-MUST-NOT-BE-READ"


@pytest.fixture
def skills_root(tmp_path: Path) -> Path:
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "private.md").write_text(SECRET, encoding="utf-8")
    root = tmp_path / "skills"
    (root / "alpha").mkdir(parents=True)
    return root


def _write_skill(root: Path, body: str) -> Path:
    path = root / "alpha" / "SKILL.md"
    path.write_text(
        "---\nname: alpha\ndescription: deployment helper for alpha\n---\n" + body,
        encoding="utf-8",
    )
    return path


@pytest.mark.parametrize(
    "ref",
    [
        pytest.param("../../outside/private.md", id="dotdot"),
        pytest.param("./../../outside/private.md", id="dot-then-dotdot"),
        pytest.param("subdir/../../../outside/private.md", id="deep-dotdot"),
    ],
)
def test_traversal_ref_is_not_loaded(skills_root: Path, ref: str):
    path = _write_skill(skills_root, f"# Alpha\nSee [notes]({ref}) for detail.\n")
    body = _load_skill_body(path, root=skills_root)
    assert SECRET not in body


def test_absolute_ref_is_not_loaded(skills_root: Path, tmp_path: Path):
    target = (tmp_path / "outside" / "private.md").resolve()
    path = _write_skill(skills_root, f"# Alpha\nSee [notes]({target.as_posix()})\n")
    body = _load_skill_body(path, root=skills_root)
    assert SECRET not in body


def test_ref_inside_root_is_still_loaded(skills_root: Path):
    (skills_root / "alpha" / "playbook.md").write_text("STEP-ONE-DEPLOY", encoding="utf-8")
    path = _write_skill(skills_root, "# Alpha\nSee [playbook](./playbook.md)\n")
    body = _load_skill_body(path, root=skills_root)
    assert "STEP-ONE-DEPLOY" in body


def test_sibling_skill_ref_inside_root_is_loaded(skills_root: Path):
    (skills_root / "beta").mkdir()
    (skills_root / "beta" / "shared.md").write_text("SHARED-CONVENTIONS", encoding="utf-8")
    path = _write_skill(skills_root, "# Alpha\nSee [shared](../beta/shared.md)\n")
    body = _load_skill_body(path, root=skills_root)
    assert "SHARED-CONVENTIONS" in body


def test_symlinked_ref_is_not_followed(skills_root: Path, tmp_path: Path):
    link = skills_root / "alpha" / "linked.md"
    try:
        link.symlink_to(tmp_path / "outside" / "private.md")
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation not permitted on this platform")
    path = _write_skill(skills_root, "# Alpha\nSee [linked](./linked.md)\n")
    body = _load_skill_body(path, root=skills_root)
    assert SECRET not in body


def test_oversized_ref_is_skipped(skills_root: Path):
    big = skills_root / "alpha" / "big.md"
    # Marker at the head: previously the file was read and truncated, so a tail
    # marker would pass even without a size check.
    big.write_text("HEAD-MARKER" + "X" * 50_000, encoding="utf-8")
    path = _write_skill(skills_root, "# Alpha\nSee [big](./big.md)\n")
    body = _load_skill_body(path, root=skills_root)
    assert "HEAD-MARKER" not in body


def test_load_matching_skills_threads_root_containment(skills_root: Path):
    _write_skill(skills_root, "# Alpha\nSee [notes](../../outside/private.md)\n")
    bodies = load_matching_skills([str(skills_root)], "deployment helper for alpha")
    assert bodies, "skill should have matched the task"
    assert SECRET not in "\n".join(bodies)
