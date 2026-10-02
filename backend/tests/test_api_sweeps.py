"""The sweep endpoints: results always arrive with their overfitting statistics."""

from __future__ import annotations


def _create(client, **overrides):
    payload = {
        "name": "spread sweep",
        "target_agent_index": 0,
        "parameters": {"spread": [0.06, 0.12, 0.24]},
        "path_count": 4,
        "steps": 90,
        "base_seed": 700,
        "agents": [
            {"name": "maker", "agent_type": "MARKET_MAKER", "config": {"quote_size": 10.0}},
            {"name": "noise", "agent_type": "NOISE_TRADER", "config": {"activity": 0.6}},
        ],
        "workers": 1,
    }
    payload.update(overrides)
    return client.post("/sweeps", json=payload)


def test_a_sweep_runs_and_reports_pbo_and_a_robust_centroid(client):
    response = _create(client)
    assert response.status_code == 202
    sweep_id = response.json()["id"]

    body = client.get(f"/sweeps/{sweep_id}").json()
    assert body["status"] == "COMPLETED"
    assert body["trial_count"] == 3
    assert 0.0 <= body["pbo"] <= 1.0
    assert body["best_cell"]["parameters"]["spread"] in (0.06, 0.12, 0.24)
    assert body["robust_centroid"]["cells_averaged"] >= 1
    assert len(body["cells"]) == 3
    assert all(cell["deflated_sharpe"] is not None for cell in body["cells"])


def test_the_surface_endpoint_packs_two_parameters(client):
    sweep_id = _create(
        client,
        parameters={"spread": [0.08, 0.16], "quote_size": [5.0, 15.0]},
    ).json()["id"]

    response = client.get(
        f"/sweeps/{sweep_id}/surface",
        params={"x": "spread", "y": "quote_size", "metric": "deflated_sharpe"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["x"] == [0.08, 0.16]
    assert body["y"] == [5.0, 15.0]
    assert len(body["values"]) == 4
    assert body["pbo"] is not None
    assert body["robust_centroid"]["parameters"]


def test_asking_for_a_parameter_that_was_not_swept_is_a_422(client):
    sweep_id = _create(client).json()["id"]
    response = client.get(
        f"/sweeps/{sweep_id}/surface", params={"x": "spread", "y": "quote_size"}
    )
    assert response.status_code == 422
    assert "not swept" in response.json()["detail"]


def test_an_unknown_metric_is_a_422(client):
    sweep_id = _create(client).json()["id"]
    response = client.get(
        f"/sweeps/{sweep_id}/surface",
        params={"x": "spread", "y": "spread", "metric": "profit"},
    )
    assert response.status_code == 422


def test_a_misspelled_swept_parameter_fails_before_the_grid_runs(client):
    response = _create(client, parameters={"spraed": [0.1, 0.2]})
    assert response.status_code == 422
    assert "unknown config keys" in response.json()["detail"]


def test_an_oversized_grid_is_refused(client):
    response = _create(
        client,
        parameters={
            "spread": [0.01 * i for i in range(1, 22)],
            "quote_size": [float(q) for q in range(1, 22)],
        },
    )
    assert response.status_code == 422
    assert "above the cap" in response.json()["detail"]


def test_a_target_index_past_the_field_is_refused(client):
    response = _create(client, target_agent_index=5)
    assert response.status_code == 422


def test_sweeps_can_be_listed_and_deleted(client):
    sweep_id = _create(client).json()["id"]
    assert any(s["id"] == sweep_id for s in client.get("/sweeps").json())
    assert client.delete(f"/sweeps/{sweep_id}").status_code == 204
    assert client.get(f"/sweeps/{sweep_id}").status_code == 404
