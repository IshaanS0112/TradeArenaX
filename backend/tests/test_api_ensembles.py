"""The ensemble endpoints."""

from __future__ import annotations


def _create(client, **overrides):
    payload = {
        "name": "noise ensemble",
        "path_count": 6,
        "base_seed": 4_000,
        "steps": 80,
        "price_process": {"initial_price": 100.0, "drift": 0.0, "volatility": 0.25},
        "agents": [
            {"name": "noise A", "agent_type": "NOISE_TRADER", "config": {"activity": 0.6}},
            {"name": "noise B", "agent_type": "NOISE_TRADER", "config": {"activity": 0.6}},
        ],
        "workers": 1,
    }
    payload.update(overrides)
    return client.post("/ensembles", json=payload)


def test_creating_an_ensemble_runs_it_and_reports_aggregates(client):
    response = _create(client)
    assert response.status_code == 202
    ensemble_id = response.json()["id"]

    # TestClient runs background tasks before returning from the context, so by the time the next.
    body = client.get(f"/ensembles/{ensemble_id}").json()
    assert body["status"] == "COMPLETED"
    assert body["completed_paths"] == 6

    distribution = client.get(f"/ensembles/{ensemble_id}/distribution").json()
    assert distribution["path_count"] == 6
    assert distribution["max_pnl_conservation_residual"] < 1e-6

    agents = distribution["agents"]
    assert len(agents) == 2
    first = next(iter(agents.values()))
    assert first["distributions"]["total_pnl"]["count"] == 6
    assert first["pnl_ci_lower"] <= first["mean_pnl"] <= first["pnl_ci_upper"]
    assert first["fraction_positive"] is not None


def test_paths_endpoint_lists_one_row_per_path(client):
    ensemble_id = _create(client).json()["id"]
    body = client.get(f"/ensembles/{ensemble_id}/paths").json()

    assert body["status"] == "COMPLETED"
    assert [row["path_index"] for row in body["paths"]] == [0, 1, 2, 3, 4, 5]
    assert body["paths"][0]["seed"] == 4_000
    assert body["paths"][3]["seed"] == 4_003


def test_an_unknown_agent_config_key_is_rejected_before_any_work(client):
    response = _create(
        client,
        agents=[{"agent_type": "MARKET_MAKER", "config": {"spraed": 0.2}}],
    )
    assert response.status_code == 422
    assert "unknown config keys" in response.json()["detail"]


def test_an_ensemble_needs_agents(client):
    response = _create(client, agents=[])
    assert response.status_code == 422


def test_distribution_before_completion_is_a_409(client):
    """A partially finished ensemble has no distribution to report."""
    from app.models import Ensemble
    from app.db.session import SessionLocal

    ensemble_id = _create(client).json()["id"]
    db = SessionLocal()
    row = db.get(Ensemble, ensemble_id)
    row.status = "RUNNING"
    row.completed_paths = 2
    db.commit()
    db.close()

    response = client.get(f"/ensembles/{ensemble_id}/distribution")
    assert response.status_code == 409
    assert "2/6" in response.json()["detail"]


def test_ensembles_can_be_listed_and_deleted(client):
    ensemble_id = _create(client).json()["id"]
    assert any(e["id"] == ensemble_id for e in client.get("/ensembles").json())

    assert client.delete(f"/ensembles/{ensemble_id}").status_code == 204
    assert client.get(f"/ensembles/{ensemble_id}").status_code == 404
