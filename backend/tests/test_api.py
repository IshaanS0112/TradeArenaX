"""API integration tests, end to end over SQLite."""

from __future__ import annotations

import pytest


def create_sim(client, **overrides):
    payload = {
        "name": "test run",
        "price_process": {
            "initial_price": 100.0,
            "drift": 0.0,
            "volatility": 0.30,
            "random_seed": 42,
        },
        "shocks": [],
    }
    payload.update(overrides)
    response = client.post("/simulations", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def add_agents(client, sim_id, types=("MARKET_MAKER", "MOMENTUM", "MEAN_REVERSION")):
    out = []
    for agent_type in types:
        r = client.post(
            f"/simulations/{sim_id}/agents",
            json={"agent_type": agent_type, "config": {}},
        )
        assert r.status_code == 201, r.text
        out.append(r.json())
    return out


def full_run(client, steps=120, **sim_overrides):
    sim = create_sim(client, **sim_overrides)
    agents = add_agents(client, sim["id"])
    r = client.post(f"/simulations/{sim['id']}/run", json={"steps": steps})
    assert r.status_code == 200, r.text
    return sim, agents, r.json()


# ------------------------------------------------------------------- basics
def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_create_and_fetch_a_simulation(client):
    sim = create_sim(client)
    assert sim["status"] == "CREATED"
    assert sim["price_process_config"]["random_seed"] == 42
    assert sim["engine_config"]["capital_base"] > 0, (
        "the engine constants must be stored with the run, or its Sharpe cannot "
        "be checked later"
    )

    fetched = client.get(f"/simulations/{sim['id']}").json()
    assert fetched["id"] == sim["id"]


def test_missing_simulation_is_a_404(client):
    assert client.get("/simulations/does-not-exist").status_code == 404


def test_list_simulations(client):
    create_sim(client, name="a")
    create_sim(client, name="b")
    names = {s["name"] for s in client.get("/simulations").json()}
    assert {"a", "b"} <= names


def test_delete_cascades(client):
    sim, _, _ = full_run(client, steps=60)
    assert client.delete(f"/simulations/{sim['id']}").status_code == 204
    assert client.get(f"/simulations/{sim['id']}").status_code == 404


@pytest.mark.parametrize(
    "bad",
    [
        {"name": ""},
        {"price_process": {"initial_price": 0.0}},
        {"price_process": {"volatility": -1.0}},
        {"shocks": [{"step": 5, "magnitude_pct": -100.0}]},
        {"shocks": [{"step": 0, "magnitude_pct": 5.0}]},
        {"shocks": [{"step": 5, "magnitude_pct": 5.0, "vol_multiplier": 0.5}]},
    ],
)
def test_invalid_simulation_payloads_are_422(client, bad):
    payload = {"name": "x", "price_process": {}, "shocks": []}
    payload.update(bad)
    assert client.post("/simulations", json=payload).status_code == 422


def test_duplicate_shock_steps_are_rejected(client):
    r = client.post(
        "/simulations",
        json={
            "name": "x",
            "shocks": [
                {"step": 10, "magnitude_pct": -5.0},
                {"step": 10, "magnitude_pct": 5.0},
            ],
        },
    )
    assert r.status_code == 422


# ------------------------------------------------------------------- agents
def test_agent_config_is_resolved_with_defaults(client):
    sim = create_sim(client)
    agent = client.post(
        f"/simulations/{sim['id']}/agents",
        json={"agent_type": "MARKET_MAKER", "config": {"spread": 0.25}},
    ).json()

    assert agent["config"]["spread"] == 0.25
    assert "inventory_skew_k" in agent["config"], (
        "storing only the supplied keys makes the run unreproducible once a "
        "default changes"
    )


def test_a_misspelled_config_key_is_rejected(client):
    sim = create_sim(client)
    r = client.post(
        f"/simulations/{sim['id']}/agents",
        json={"agent_type": "MOMENTUM", "config": {"threshhold": 0.01}},
    )
    assert r.status_code == 422
    assert "unknown config keys" in r.text


def test_an_incoherent_config_value_is_rejected(client):
    sim = create_sim(client)
    r = client.post(
        f"/simulations/{sim['id']}/agents",
        json={"agent_type": "MARKET_MAKER", "config": {"spread": -1.0}},
    )
    assert r.status_code == 422


def test_unknown_agent_type_is_rejected(client):
    sim = create_sim(client)
    r = client.post(
        f"/simulations/{sim['id']}/agents",
        json={"agent_type": "SCALPER", "config": {}},
    )
    assert r.status_code == 422


def test_agents_get_default_names(client):
    sim = create_sim(client)
    agents = add_agents(client, sim["id"], types=("MOMENTUM", "MOMENTUM"))
    assert agents[0]["name"] != agents[1]["name"]


def test_adding_an_agent_invalidates_a_completed_run(client):
    sim, _, _ = full_run(client, steps=60)
    assert client.get(f"/simulations/{sim['id']}").json()["status"] == "COMPLETED"

    client.post(
        f"/simulations/{sim['id']}/agents", json={"agent_type": "MOMENTUM", "config": {}}
    )
    refetched = client.get(f"/simulations/{sim['id']}").json()
    assert refetched["status"] == "CREATED", (
        "old results no longer describe the current agent set"
    )


# ---------------------------------------------------------------------- run
def test_running_with_no_agents_is_422(client):
    sim = create_sim(client)
    r = client.post(f"/simulations/{sim['id']}/run", json={"steps": 50})
    assert r.status_code == 422
    assert "at least one agent" in r.text


def test_a_run_returns_a_summary_with_metrics(client):
    _, agents, summary = full_run(client, steps=150)

    assert summary["steps_run"] == 150
    assert summary["total_trades"] > 0
    assert set(summary["agent_metrics"]) == {a["id"] for a in agents}
    for metrics in summary["agent_metrics"].values():
        assert "sharpe_ratio" in metrics
        assert "max_drawdown_pct" in metrics
        assert "name" in metrics


def test_a_shock_past_the_end_of_the_run_is_rejected(client):
    sim = create_sim(client, shocks=[{"step": 500, "magnitude_pct": -10.0}])
    add_agents(client, sim["id"])
    r = client.post(f"/simulations/{sim['id']}/run", json={"steps": 100})
    assert r.status_code == 422
    assert "never fire" in r.text


def test_step_count_bounds_are_enforced_at_the_boundary(client):
    sim = create_sim(client)
    add_agents(client, sim["id"])
    assert client.post(f"/simulations/{sim['id']}/run", json={"steps": 0}).status_code == 422
    assert (
        client.post(f"/simulations/{sim['id']}/run", json={"steps": 999_999}).status_code
        == 422
    )


def test_downsampling_reduces_stored_series_without_losing_the_last_step(client):
    sim = create_sim(client)
    add_agents(client, sim["id"], types=("MARKET_MAKER", "MOMENTUM"))
    client.post(
        f"/simulations/{sim['id']}/run", json={"steps": 200, "persist_every_n_steps": 10}
    )

    comparison = client.get(f"/simulations/{sim['id']}/comparison").json()
    steps = [p["step"] for p in comparison["market"]]
    assert len(steps) < 200
    assert steps[-1] == 200, "the final step must survive downsampling"


def test_a_shock_step_is_never_downsampled_away(client):
    sim = create_sim(client, shocks=[{"step": 77, "magnitude_pct": -12.0}])
    add_agents(client, sim["id"], types=("MARKET_MAKER", "MOMENTUM"))
    client.post(
        f"/simulations/{sim['id']}/run", json={"steps": 200, "persist_every_n_steps": 25}
    )

    comparison = client.get(f"/simulations/{sim['id']}/comparison").json()
    shock_points = [p for p in comparison["market"] if p["shock_fired"]]
    assert [p["step"] for p in shock_points] == [77], (
        "dropping the shock step because it fell between samples makes the chart "
        "lie about what happened"
    )


# -------------------------------------------------------------- order book
def test_book_snapshot_requires_a_completed_run(client):
    sim = create_sim(client)
    add_agents(client, sim["id"])
    r = client.get(f"/simulations/{sim['id']}/order-book/snapshot")
    assert r.status_code == 409


def test_book_snapshot_shape(client):
    sim, _, _ = full_run(client, steps=150)
    snap = client.get(f"/simulations/{sim['id']}/order-book/snapshot").json()

    assert snap["reconstructed_at_step"] == 150
    for side in ("bids", "asks"):
        for level in snap[side]:
            assert level["quantity"] > 0
            assert level["order_count"] >= 1
    bids = [lvl["price"] for lvl in snap["bids"]]
    assert bids == sorted(bids, reverse=True), "best bid first"
    if snap["best_bid"] and snap["best_ask"]:
        assert snap["best_bid"] < snap["best_ask"], "a served book must not be crossed"


def test_book_snapshot_at_an_earlier_step(client):
    sim, _, _ = full_run(client, steps=150)
    snap = client.get(
        f"/simulations/{sim['id']}/order-book/snapshot", params={"step": 40}
    ).json()
    assert snap["reconstructed_at_step"] == 40


def test_book_snapshot_past_the_end_is_422(client):
    sim, _, _ = full_run(client, steps=100)
    r = client.get(
        f"/simulations/{sim['id']}/order-book/snapshot", params={"step": 5000}
    )
    assert r.status_code == 422


# -------------------------------------------------------------- performance
def test_agent_performance_series(client):
    sim, agents, _ = full_run(client, steps=150)
    agent_id = agents[0]["id"]
    body = client.get(f"/simulations/{sim['id']}/agents/{agent_id}/performance").json()

    assert body["agent_id"] == agent_id
    assert len(body["series"]) == 150
    assert [p["step"] for p in body["series"]] == list(range(1, 151))
    for point in body["series"]:
        assert point["total_pnl"] == pytest.approx(
            point["realized_pnl"] + point["unrealized_pnl"], abs=1e-6
        )


def test_every_performance_row_records_the_mark_it_used(client):
    """An unrealized PnL whose mark cannot be recovered is not auditable.

    The mid price is null on a one-sided book, but the engine still had to mark
    open inventory against *something* - the last trade. Storing the null mid
    would leave those rows unexplainable.
    """
    sim, agents, _ = full_run(client, steps=200)
    body = client.get(
        f"/simulations/{sim['id']}/agents/{agents[0]['id']}/performance"
    ).json()

    assert all(p["mark_price"] is not None for p in body["series"])

    comparison = client.get(f"/simulations/{sim['id']}/comparison").json()
    one_sided = [p for p in comparison["market"] if p["mid_price"] is None]
    marks = {p["step"]: p["mark_price"] for p in body["series"]}
    for point in one_sided:
        assert marks[point["step"]] is not None, (
            "a step with no mid still has a mark, and it has to be stored"
        )


def test_performance_for_an_agent_in_another_simulation_is_404(client):
    sim_a, agents_a, _ = full_run(client, steps=60)
    sim_b, _, _ = full_run(client, steps=60)
    r = client.get(f"/simulations/{sim_b['id']}/agents/{agents_a[0]['id']}/performance")
    assert r.status_code == 404


# --------------------------------------------------------------- comparison
def test_comparison_ranks_agents_and_reports_the_conservation_check(client):
    sim, agents, _ = full_run(client, steps=200)
    body = client.get(f"/simulations/{sim['id']}/comparison").json()

    assert len(body["rows"]) == len(agents)
    pnls = [r["total_pnl"] for r in body["rows"]]
    assert pnls == sorted(pnls, reverse=True), "best PnL first"

    assert body["pnl_conservation_residual"] == pytest.approx(0.0, abs=1e-6), (
        "a non-zero residual is an accounting bug, not a profit"
    )
    assert body["steps_run"] == 200
    for row in body["rows"]:
        assert row["agent_type"] in {"MARKET_MAKER", "MOMENTUM", "MEAN_REVERSION"}


def test_comparison_exposes_the_shock_steps_for_annotation(client):
    sim = create_sim(client, shocks=[{"step": 50, "magnitude_pct": -10.0}])
    add_agents(client, sim["id"])
    client.post(f"/simulations/{sim['id']}/run", json={"steps": 150})

    body = client.get(f"/simulations/{sim['id']}/comparison").json()
    assert body["shock_steps"] == [50]


def test_comparison_requires_a_completed_run(client):
    sim = create_sim(client)
    add_agents(client, sim["id"])
    assert client.get(f"/simulations/{sim['id']}/comparison").status_code == 409


# --------------------------------------------------------------------- meta
def test_agent_defaults_endpoint_lists_every_tunable(client):
    body = client.get("/meta/agent-defaults").json()
    assert set(body) == {"MARKET_MAKER", "MOMENTUM", "MEAN_REVERSION"}
    assert "inventory_skew_k" in body["MARKET_MAKER"]
    assert "z_threshold" in body["MEAN_REVERSION"]


def test_engine_config_endpoint_publishes_the_formulas(client):
    body = client.get("/meta/engine-config").json()
    assert "sharpe" in body["formulas"]
    assert "no live market data" in body["authenticity"].lower()
    assert body["engine"]["capital_base"] > 0


def test_openapi_schema_is_valid(client):
    schema = client.get("/openapi.json").json()
    assert "/simulations/{simulation_id}/comparison" in schema["paths"]


# ------------------------------------------------------- reproducibility
def test_two_runs_with_the_same_seed_report_the_same_pnl(client):
    _, agents_a, summary_a = full_run(client, steps=150)
    _, agents_b, summary_b = full_run(client, steps=150)

    by_type_a = {m["name"].split("-")[0]: m["total_pnl"] for m in summary_a["agent_metrics"].values()}
    by_type_b = {m["name"].split("-")[0]: m["total_pnl"] for m in summary_b["agent_metrics"].values()}
    assert by_type_a == by_type_b


def test_a_different_seed_changes_the_outcome(client):
    _, _, summary_a = full_run(client, steps=150)
    _, _, summary_b = full_run(
        client,
        steps=150,
        price_process={
            "initial_price": 100.0,
            "drift": 0.0,
            "volatility": 0.30,
            "random_seed": 777,
        },
    )
    assert summary_a["final_reference_price"] != summary_b["final_reference_price"]
