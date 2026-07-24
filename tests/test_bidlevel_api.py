"""API smoke tests: the full workflow through HTTP."""
import os

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ["BIDLEVEL_SKIP_INIT"] = "1"

from bidlevel.db import Base, get_db  # noqa: E402
from bidlevel.main import app  # noqa: E402


@pytest.fixture()
def client():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def override():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def test_health_and_frontend(client):
    assert client.get("/health").json()["app"] == "bidlevel"
    landing = client.get("/")
    assert landing.status_code == 200 and "Level every bid" in landing.text
    workspace = client.get("/app")
    assert workspace.status_code == 200 and "Leveling workspace" in workspace.text
    css = client.get("/assets/theme.css")
    assert css.status_code == 200 and "--color-accent" in css.text
    font = client.get("/assets/fonts/BarlowCondensed-600.woff2")
    assert font.status_code == 200 and len(font.content) > 10000


def test_project_validation_errors(client):
    r = client.post("/api/v1/projects", json={"name": "X"})
    assert r.status_code == 422
    assert any("number" in e for e in r.json()["detail"]["errors"])


def test_end_to_end_via_api(client):
    seed = client.post("/api/v1/demo/seed").json()
    pid, pkg_id = seed["project_id"], seed["electrical_package_id"]

    docs = client.get(f"/api/v1/projects/{pid}/documents").json()
    assert all(d["processing_status"] == "complete" for d in docs)
    sheets = client.get(f"/api/v1/projects/{pid}/sheets").json()
    assert any(s["number"] == "A-101" and s["discipline"] == "Architecture" for s in sheets)
    qtys = client.get(f"/api/v1/projects/{pid}/quantities").json()
    assert any(q["unit"] == "CY" for q in qtys)
    trades = client.get(f"/api/v1/projects/{pid}/trades").json()
    assert any(t["trade"] == "Electrical" for t in trades)

    grid = client.get(f"/api/v1/packages/{pkg_id}/leveling").json()
    assert len(grid["bidders"]) == 3 and grid["scope_rows"]

    risks = client.get(f"/api/v1/packages/{pkg_id}/risk").json()
    assert risks and all({"severity", "explanation", "suggested_action"} <= set(r) for r in risks)
    upd = client.patch(f"/api/v1/risk/{risks[0]['id']}",
                       json={"status": "acknowledged"}).json()
    assert upd["status"] == "acknowledged"

    rec = client.get(f"/api/v1/packages/{pkg_id}/award/recommendation").json()
    assert rec["recommendation"]
    a = client.post(f"/api/v1/packages/{pkg_id}/award",
                    json={"proposal_id": rec["recommendation"]["proposal_id"],
                          "user": "estimator"}).json()
    ap = client.post(f"/api/v1/awards/{a['id']}/approvals",
                     json={"approver": "VP", "decision": "approved"}).json()
    assert ap["status"] == "approved"

    dash = client.get("/api/v1/analytics").json()
    assert dash["projects"] == 1 and dash["invitations"] >= 3
    assert dash["participation_rate"] is not None

    audit = client.get("/api/v1/audit").json()
    assert isinstance(audit, list)


def test_portal_token_submission(client):
    seed = client.post("/api/v1/demo/seed").json()
    pkg_id = seed["electrical_package_id"]
    invs = client.get(f"/api/v1/packages/{pkg_id}/invitations").json()
    token = invs[0]["portal_token"]
    r = client.post(f"/api/v1/portal/{token}/proposals",
                    json={"text": "Base Bid: $2,300,000", "submitted_by": "sub@example.com"})
    assert r.status_code == 200
    body = r.json()
    assert body["base_bid"] == 2_300_000 and body["version"] == 2
    bad = client.post("/api/v1/portal/not-a-token/proposals", json={"text": "x"})
    assert bad.status_code == 404
