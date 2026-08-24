"""API contract: tenancy, plan enforcement and the production lifecycle."""
from __future__ import annotations


async def test_health_and_capabilities_need_no_auth(client):
    assert (await client.get("/health")).status_code == 200
    caps = await client.get("/capabilities")
    assert caps.status_code == 200
    assert caps.json()["modalities"]["video"]["available"] >= 1


async def test_public_pricing_is_public(client):
    resp = await client.get("/api/v1/billing/plans")
    assert resp.status_code == 200
    tiers = {p["tier"] for p in resp.json()["plans"]}
    assert {"free", "starter", "studio", "scale"} <= tiers


async def test_protected_routes_reject_anonymous_callers(client):
    for path in ("/api/v1/series", "/api/v1/productions", "/api/v1/billing/account"):
        assert (await client.get(path)).status_code == 401, path


async def test_signup_grants_the_free_allowance(authed_client):
    me = (await authed_client.get("/api/v1/auth/me")).json()
    assert me["plan"] == "free"
    assert me["credit_balance"] == 300


async def test_duplicate_signup_is_rejected(client, authed_client):
    resp = await client.post("/api/v1/auth/signup", json={
        "email": authed_client.email, "password": "supersecret123",
    })
    assert resp.status_code == 409


async def test_login_with_a_bad_password_fails_the_same_way(client, authed_client):
    resp = await client.post("/api/v1/auth/login",
                             json={"email": authed_client.email, "password": "nope-nope-nope"})
    assert resp.status_code == 401


async def test_series_and_episode_lifecycle(authed_client):
    created = await authed_client.post("/api/v1/series", json={
        "title": "Slack Water",
        "brief": "A harbour town drama about debts that are never really repaid, "
                 "centred on a fixer who has run out of favours.",
        "genre": "crime drama",
    })
    assert created.status_code == 201, created.text
    series_id = created.json()["id"]

    listing = await authed_client.get("/api/v1/series")
    assert [s["id"] for s in listing.json()] == [series_id]

    episode = await authed_client.post(f"/api/v1/series/{series_id}/episodes", json={
        "premise": "One night to return a favour", "target_minutes": 2,
    })
    assert episode.status_code == 201, episode.text
    assert episode.json()["number"] == 1

    bible = await authed_client.get(f"/api/v1/series/{series_id}/bible")
    assert bible.json()["status"] == "not_generated"


async def test_tenants_cannot_read_each_others_series(client, authed_client):
    created = await authed_client.post("/api/v1/series", json={
        "title": "Private", "brief": "A brief that belongs to exactly one organisation only.",
    })
    series_id = created.json()["id"]

    other = await client.post("/api/v1/auth/signup", json={
        "email": "intruder@example.com", "password": "supersecret123",
    })
    headers = {"Authorization": f"Bearer {other.json()['access_token']}"}
    resp = await client.get(f"/api/v1/series/{series_id}", headers=headers)
    assert resp.status_code == 404, "cross-tenant read must be indistinguishable from missing"


async def test_quote_reports_the_routing_chain(authed_client):
    resp = await authed_client.post("/api/v1/productions/quote",
                                    json={"target_minutes": 2, "profile": "free"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estimated_shots"] > 0
    assert body["routing_plan"]["shot_video"]


async def test_free_plan_cannot_quote_flagship(authed_client):
    resp = await authed_client.post("/api/v1/productions/quote",
                                    json={"target_minutes": 2, "profile": "flagship"})
    assert resp.status_code == 403


async def test_free_plan_runtime_cap_is_enforced(authed_client):
    series = (await authed_client.post("/api/v1/series", json={
        "title": "Long", "brief": "A brief long enough to satisfy the minimum length rule here.",
    })).json()
    resp = await authed_client.post("/api/v1/productions", json={
        "series_id": series["id"], "target_minutes": 15, "profile": "free", "resolution": "720p",
    })
    assert resp.status_code == 422
    assert "caps episodes" in resp.json()["error"]["message"]


async def test_production_queues_and_reserves_credits(authed_client):
    series = (await authed_client.post("/api/v1/series", json={
        "title": "Queued", "brief": "A brief long enough to satisfy the minimum length rule here.",
    })).json()
    resp = await authed_client.post("/api/v1/productions", json={
        "series_id": series["id"], "target_minutes": 1, "profile": "offline", "resolution": "480p",
    })
    assert resp.status_code == 202, resp.text
    job = resp.json()
    assert job["status"] == "queued"

    detail = await authed_client.get(f"/api/v1/productions/{job['id']}")
    assert detail.json()["id"] == job["id"]

    cancelled = await authed_client.post(f"/api/v1/productions/{job['id']}/cancel")
    assert cancelled.json()["status"] == "cancelled"

    me = (await authed_client.get("/api/v1/auth/me")).json()
    assert me["credit_balance"] == 300, "cancelling before start must refund in full"


async def test_concurrency_limit_is_enforced(authed_client):
    series = (await authed_client.post("/api/v1/series", json={
        "title": "Busy", "brief": "A brief long enough to satisfy the minimum length rule here.",
    })).json()
    body = {"series_id": series["id"], "target_minutes": 1,
            "profile": "offline", "resolution": "480p"}
    assert (await authed_client.post("/api/v1/productions", json=body)).status_code == 202
    second = await authed_client.post("/api/v1/productions", json=body)
    assert second.status_code == 409
    assert "already running" in second.json()["error"]["message"]


async def test_unknown_model_override_is_rejected(authed_client):
    series = (await authed_client.post("/api/v1/series", json={
        "title": "Override", "brief": "A brief long enough to satisfy the minimum length rule.",
    })).json()
    resp = await authed_client.post("/api/v1/productions", json={
        "series_id": series["id"], "target_minutes": 1, "profile": "offline",
        "resolution": "480p", "model_overrides": {"shot_video": "not/a-real-model"},
    })
    assert resp.status_code == 422


async def test_api_key_requires_a_paid_plan(authed_client):
    created = await authed_client.post("/api/v1/auth/api-keys", json={"name": "ci"})
    assert created.status_code == 201
    secret = created.json()["secret"]
    assert secret and secret.startswith("nmk_")

    resp = await authed_client.get("/api/v1/auth/me",
                                   headers={"X-API-Key": secret, "Authorization": ""})
    assert resp.status_code == 403, "free plans must not get programmatic access"


async def test_model_catalog_is_browsable(authed_client):
    resp = await authed_client.get("/api/v1/models?modality=video")
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] >= 5
    assert any(m["capabilities"]["reference_images"] for m in body["models"])

    profiles = await authed_client.get("/api/v1/models/routing/profiles")
    names = {p["name"] for p in profiles.json()["profiles"]}
    assert {"free", "balanced", "premium", "flagship"} <= names
    free = next(p for p in profiles.json()["profiles"] if p["name"] == "free")
    assert free["available_to_plan"] is True
    flagship = next(p for p in profiles.json()["profiles"] if p["name"] == "flagship")
    assert flagship["available_to_plan"] is False
