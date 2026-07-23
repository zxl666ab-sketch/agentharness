import pytest

from agentharness.security.sandbox import SandboxError, assert_in_workspace


def test_path_within_cwd(tmp_path):
    f = tmp_path / "ok.txt"
    f.write_text("x")
    p = assert_in_workspace("ok.txt", cwd=tmp_path)
    assert p == f.resolve()


def test_path_traversal_blocked(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope")
    with pytest.raises(SandboxError):
        assert_in_workspace("../outside.txt", cwd=tmp_path)


def test_absolute_outside_blocked(tmp_path):
    with pytest.raises(SandboxError):
        assert_in_workspace(str(tmp_path.parent / "x"), cwd=tmp_path)
