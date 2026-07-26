"""The SPA catch-all must never serve a file from outside the build directory.

``FileResponse`` bypasses the redaction layer, so an escape here is an unredacted
arbitrary host-file read — and ``agentharness web --host 0.0.0.0`` makes it remote.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from agentharness.api.server import create_app
from agentharness.harness import Harness

SECRET = "TOP-SECRET-OUTSIDE-DIST"


@pytest.fixture
def dist_with_secret_sibling(tmp_path: Path) -> Path:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html>spa</html>", encoding="utf-8")
    (dist / "app.js").write_text("console.log('ok')", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(SECRET, encoding="utf-8")
    return dist


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/%2e%2e/secret.txt", id="encoded-dotdot"),
        pytest.param("/%2e%2e%2fsecret.txt", id="encoded-dotdot-slash"),
        pytest.param("/assets/%2e%2e/%2e%2e/secret.txt", id="nested-encoded"),
        pytest.param("/..%2fsecret.txt", id="mixed-encoding"),
    ],
)
async def test_encoded_traversal_never_leaks_file_outside_dist(
    path: str, data_dir, dist_with_secret_sibling: Path
):
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h, web_dist=dist_with_secret_sibling)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(path)

    # Either rejected outright or served the SPA shell — never the sibling file.
    assert SECRET not in response.text
    h.close()


@pytest.mark.asyncio
async def test_absolute_path_is_not_served(data_dir, dist_with_secret_sibling: Path):
    secret = dist_with_secret_sibling.parent / "secret.txt"
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h, web_dist=dist_with_secret_sibling)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(f"/{secret.as_posix()}")

    assert SECRET not in response.text
    h.close()


@pytest.mark.asyncio
async def test_real_asset_inside_dist_is_still_served(
    data_dir, dist_with_secret_sibling: Path
):
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h, web_dist=dist_with_secret_sibling)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/app.js")

    assert response.status_code == 200
    assert "console.log" in response.text
    h.close()


@pytest.mark.asyncio
async def test_unknown_ui_route_falls_back_to_spa(
    data_dir, dist_with_secret_sibling: Path
):
    h = Harness(data_dir=data_dir)
    app = create_app(harness=h, web_dist=dist_with_secret_sibling)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/runs/abc123")

    assert response.status_code == 200
    assert "spa" in response.text
    h.close()
