from .conftest import bootstrap_tenant, login_after_reset


def _admin(boot):
    return next(u for u in boot["users"] if u["role"] == "Admin")


def test_login_wrong_password_rejected(client):
    boot = bootstrap_tenant(client)
    r = client.post("/auth/login", json={"email": _admin(boot)["email"], "password": "wrong-password"})
    assert r.status_code == 401


def test_must_reset_gate_blocks_everything_except_reset(client):
    boot = bootstrap_tenant(client)
    admin = _admin(boot)
    tokens = client.post(
        "/auth/login", json={"email": admin["email"], "password": admin["temp_password"]}
    ).json()
    assert tokens["must_reset"] is True

    r = client.get("/users", headers={"Authorization": f"Bearer {tokens['access_token']}"})
    assert r.status_code == 403
    assert r.json()["detail"]["code"] == "MUST_RESET"


def test_login_reset_refresh_flow(client):
    boot = bootstrap_tenant(client)
    admin = _admin(boot)

    fresh = login_after_reset(client, admin["email"], admin["temp_password"], "N3w-password!")
    assert fresh["must_reset"] is False

    # old temp password no longer works, new one does
    assert client.post("/auth/login", json={"email": admin["email"], "password": admin["temp_password"]}).status_code == 401
    relogin = client.post("/auth/login", json={"email": admin["email"], "password": "N3w-password!"})
    assert relogin.status_code == 200
    assert relogin.json()["must_reset"] is False

    # gate is lifted
    r = client.get("/users", headers={"Authorization": f"Bearer {fresh['access_token']}"})
    assert r.status_code == 200
    assert len(r.json()) == 2

    # refresh issues a working token pair
    r = client.post("/auth/refresh", json={"refresh_token": fresh["refresh_token"]})
    assert r.status_code == 200
    r2 = client.get("/users", headers={"Authorization": f"Bearer {r.json()['access_token']}"})
    assert r2.status_code == 200


def test_refresh_token_rejected_as_access_token(client):
    boot = bootstrap_tenant(client)
    admin = _admin(boot)
    fresh = login_after_reset(client, admin["email"], admin["temp_password"])
    r = client.get("/users", headers={"Authorization": f"Bearer {fresh['refresh_token']}"})
    assert r.status_code == 401


def test_reset_password_requires_auth_or_token(client):
    r = client.post("/auth/reset-password", json={"new_password": "whatever-123"})
    assert r.status_code == 401


def test_short_password_rejected(client):
    boot = bootstrap_tenant(client)
    admin = _admin(boot)
    tokens = client.post(
        "/auth/login", json={"email": admin["email"], "password": admin["temp_password"]}
    ).json()
    r = client.post(
        "/auth/reset-password",
        json={"new_password": "short"},
        headers={"Authorization": f"Bearer {tokens['access_token']}"},
    )
    assert r.status_code == 422
