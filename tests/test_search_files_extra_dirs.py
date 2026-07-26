"""search_files must cover extra_dirs, like read_file and write_file already do.

Walking only cwd made a granted extra directory look empty, which is worse than a
refusal: the agent concludes the content is not there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agentharness.contracts import ToolContext
from agentharness.tools.fs import SearchFilesTool, _search_roots


def _ctx(workspace: Path, data_dir: Path, extra: list[str] | None = None) -> ToolContext:
    return ToolContext(
        run_id="r",
        session_id="s",
        cwd=str(workspace),
        data_dir=str(data_dir),
        extra_dirs=extra or [],
    )


@pytest.mark.asyncio
async def test_hit_in_extra_dir_is_found(workspace: Path, data_dir: Path, tmp_path: Path):
    extra = tmp_path / "granted"
    extra.mkdir()
    (extra / "notes.txt").write_text("UNIQUE-EXTRA-NEEDLE\n", encoding="utf-8")

    result = await SearchFilesTool().run(
        _ctx(workspace, data_dir, [str(extra)]), {"query": "UNIQUE-EXTRA-NEEDLE"}
    )

    assert not result.is_error
    assert "UNIQUE-EXTRA-NEEDLE" in result.content


@pytest.mark.asyncio
async def test_extra_dir_is_not_searched_when_not_granted(
    workspace: Path, data_dir: Path, tmp_path: Path
):
    ungranted = tmp_path / "ungranted"
    ungranted.mkdir()
    (ungranted / "secret.txt").write_text("MUST-NOT-APPEAR\n", encoding="utf-8")

    result = await SearchFilesTool().run(
        _ctx(workspace, data_dir), {"query": "MUST-NOT-APPEAR"}
    )

    assert "MUST-NOT-APPEAR" not in result.content


@pytest.mark.asyncio
async def test_cwd_hits_still_reported_relative(workspace: Path, data_dir: Path):
    (workspace / "inside.txt").write_text("LOCAL-NEEDLE\n", encoding="utf-8")

    result = await SearchFilesTool().run(
        _ctx(workspace, data_dir), {"query": "LOCAL-NEEDLE"}
    )

    assert "inside.txt:1:LOCAL-NEEDLE" in result.content


@pytest.mark.asyncio
async def test_both_roots_are_searched_together(
    workspace: Path, data_dir: Path, tmp_path: Path
):
    extra = tmp_path / "granted"
    extra.mkdir()
    (workspace / "a.txt").write_text("SHARED-TOKEN in cwd\n", encoding="utf-8")
    (extra / "b.txt").write_text("SHARED-TOKEN in extra\n", encoding="utf-8")

    result = await SearchFilesTool().run(
        _ctx(workspace, data_dir, [str(extra)]),
        {"query": "SHARED-TOKEN", "max_results": 10},
    )

    assert "in cwd" in result.content
    assert "in extra" in result.content


def test_search_roots_drops_nested_and_missing_dirs(tmp_path: Path):
    cwd = tmp_path / "ws"
    (cwd / "nested").mkdir(parents=True)
    sibling = tmp_path / "sibling"
    sibling.mkdir()

    roots = _search_roots(
        cwd.resolve(),
        [str(cwd / "nested"), str(sibling), str(tmp_path / "does-not-exist"), str(cwd)],
    )

    assert roots == [cwd.resolve(), sibling.resolve()]
