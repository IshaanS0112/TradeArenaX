"""The endpoints that expose latency and adverse selection."""

from __future__ import annotations


def _simulation(client, name="latency run", shock_step=60):
    response = client.post(
        "/simulations",
        json={
            "name": name,
            "price_process": {
                "initial_price": 100.0,
                "drift": 0.0,
                "volatility": 0.35,
                "random_seed": 42,
            },
            "shocks": [
                {
                    "step": shock_step,
                    "magnitude_pct": -8.0,
                    "vol_multiplier": 3.0,
                    "vol_half_life_steps": 30,
                }
            ],
        },
    )
    assert response.status_code == 201
    return response.json()["id"]


def test_agents_accept_a_latency_profile(client):
    sim_id = _simulation(client)
    response = client.post(
        f"/simulations/{sim_id}/agents",
        json={
            "agent_type": "MARKET_MAKER",
            "config": {},
            "latency": {"latency_in_us": 500, "latency_out_us": 250_000},
        },
    )
    assert response.status_code == 201
    body = response.json()
    assert body["latency_config"] == {
        "latency_in_us": 500,
        "latency_out_us": 250_000,
        "latency_jitter_us": 0.0,
    }


def test_a_misspelled_latency_key_is_a_422(client):
    sim_id = _simulation(client)
    response = client.post(
        f"/simulations/{sim_id}/agents",
        json={"agent_type": "MARKET_MAKER", "latency": {"latency_our_us": 5}},
    )
    assert response.status_code == 422
    assert "unknown latency config key" in response.json()["detail"]


def test_microstructure_endpoint_reports_the_decomposition(client):
    sim_id = _simulation(client)
    for agent_type in ("MARKET_MAKER", "MOMENTUM", "MEAN_REVERSION"):
        client.post(f"/simulations/{sim_id}/agents", json={"agent_type": agent_type})
    client.post(f"/simulations/{sim_id}/run", json={"steps": 150})

    response = client.get(f"/simulations/{sim_id}/microstructure")
    assert response.status_code == 200
    body = response.json()

    assert body["default_horizon_steps"] in body["horizons"]
    horizon = str(body["default_horizon_steps"])
    market = body["market"][horizon]
    assert market["trade_count"] > 0
    assert abs(
        market["effective_half_spread"]
        - (market["realised_half_spread"] + market["price_impact"])
    ) < 1e-9

    rows = body["by_agent"][horizon]
    assert rows, "a run with trades must have per-agent rows"
    assert {r["role"] for r in rows} <= {"maker", "taker"}
    assert all(r["name"] for r in rows), "rows carry the agent's name, not just its id"


def test_microstructure_requires_a_completed_run(client):
    sim_id = _simulation(client)
    client.post(f"/simulations/{sim_id}/agents", json={"agent_type": "MARKET_MAKER"})
    response = client.get(f"/simulations/{sim_id}/microstructure")
    assert response.status_code == 409


def test_latency_races_endpoint_explains_a_contested_fill(client):
    sim_id = _simulation(client)
    client.post(
        f"/simulations/{sim_id}/agents",
        json={
            "name": "slow maker",
            "agent_type": "MARKET_MAKER",
            "latency": {"latency_out_us": 250_000},
        },
    )
    for agent_type in ("MOMENTUM", "MEAN_REVERSION"):
        client.post(
            f"/simulations/{sim_id}/agents",
            json={"agent_type": agent_type, "latency": {"latency_out_us": 1_000}},
        )
    client.post(f"/simulations/{sim_id}/run", json={"steps": 200})

    response = client.get(f"/simulations/{sim_id}/latency-races")
    assert response.status_code == 200
    body = response.json()

    assert body["profiles"], "the configured latency travels with the result"
    assert body["races"], "a slow maker against fast takers loses races"
    race = body["races"][0]
    assert race["maker_name"] == "slow maker"
    assert race["cancel_arrival_us"] > race["fill_us"], "the cancel arrived too late"
    assert race["margin_us"] > 0
    assert body["adverse_fills"][race["maker_agent_id"]] >= 1


def test_zero_latency_run_has_no_races(client):
    sim_id = _simulation(client)
    for agent_type in ("MARKET_MAKER", "MOMENTUM"):
        client.post(f"/simulations/{sim_id}/agents", json={"agent_type": agent_type})
    client.post(f"/simulations/{sim_id}/run", json={"steps": 120})

    body = client.get(f"/simulations/{sim_id}/latency-races").json()
    assert body["races"] == []
    assert set(body["adverse_fills"].values()) == {0}


def test_engine_config_records_the_horizon_and_step_duration(client):
    """A stored realised spread is only interpretable with its horizon."""
    body = client.get("/meta/engine-config").json()
    assert body["engine"]["spread_horizon_steps"] >= 1
    assert body["engine"]["step_duration_us"] > 0


def test_greeks_attribution_endpoint_reports_the_hedged_book(client):
    sim_id = _simulation(client, name="options run")
    client.post(f"/simulations/{sim_id}/agents", json={"agent_type": "MARKET_MAKER"})
    client.post(
        f"/simulations/{sim_id}/agents",
        json={
            "name": "options desk",
            "agent_type": "OPTIONS_MAKER",
            "config": {"client_intensity": 0.3, "hedge_band": 20.0},
        },
    )
    client.post(f"/simulations/{sim_id}/agents", json={"agent_type": "NOISE_TRADER"})
    client.post(f"/simulations/{sim_id}/run", json={"steps": 150})

    response = client.get(f"/simulations/{sim_id}/greeks/attribution")
    assert response.status_code == 200
    body = response.json()

    agent_id = next(iter(body["agents"]))
    assert body["names"][agent_id] == "options desk"
    attribution = body["agents"][agent_id]
    assert attribution["snapshots"], "the per-step series travels with the totals"
    assert set(attribution) >= {
        "gamma_pnl",
        "vega_pnl",
        "theta_pnl",
        "hedge_slippage",
        "option_premium",
        "hedge_trades",
    }


def test_a_run_with_no_options_maker_has_nothing_to_attribute(client):
    sim_id = _simulation(client, name="equity only")
    client.post(f"/simulations/{sim_id}/agents", json={"agent_type": "MARKET_MAKER"})
    client.post(f"/simulations/{sim_id}/agents", json={"agent_type": "MOMENTUM"})
    client.post(f"/simulations/{sim_id}/run", json={"steps": 80})

    response = client.get(f"/simulations/{sim_id}/greeks/attribution")
    assert response.status_code == 409
    assert "no options maker" in response.json()["detail"]
