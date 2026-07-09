from .conftest import bootstrap_tenant, login_after_reset


def _admin(boot):
    return next(u for u in boot["users"] if u["role"] == "Admin")


def test_cross_tenant_isolation(client):
    boot_a = bootstrap_tenant(client, email="hello@alphaledger.ae", user_email_domain="alphaledger.ae")
    boot_b = bootstrap_tenant(client, email="hello@betabooks.ae", user_email_domain="betabooks.ae")

    admin_a = _admin(boot_a)
    tokens_a = login_after_reset(client, admin_a["email"], admin_a["temp_password"])
    headers_a = {"Authorization": f"Bearer {tokens_a['access_token']}"}

    # user A cannot read tenant B's user — 404, not 403 (existence must not leak)
    user_b_id = boot_b["users"][0]["id"]
    r = client.get(f"/users/{user_b_id}", headers=headers_a)
    assert r.status_code == 404

    # list is scoped to tenant A only
    r = client.get("/users", headers=headers_a)
    assert r.status_code == 200
    emails = {u["email"] for u in r.json()}
    assert emails == {u["email"] for u in boot_a["users"]}
    assert not emails & {u["email"] for u in boot_b["users"]}

    # admin A cannot mutate tenant B's user
    assert client.patch(f"/users/{user_b_id}", json={"name": "Hacked"}, headers=headers_a).status_code == 404
    assert client.post(f"/users/{user_b_id}/deactivate", headers=headers_a).status_code == 404


def test_admin_only_user_management(client):
    boot = bootstrap_tenant(client)
    staff = next(u for u in boot["users"] if u["role"] == "Staff")
    tokens = login_after_reset(client, staff["email"], staff["temp_password"])
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    r = client.post(
        "/users",
        json={"name": "Newbie", "email": "newbie@alphaledger.ae", "role": "Staff"},
        headers=headers,
    )
    assert r.status_code == 403

    admin = next(u for u in boot["users"] if u["role"] == "Admin")
    admin_tokens = login_after_reset(client, admin["email"], admin["temp_password"], "Adm1n-pass!")
    admin_headers = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

    r = client.post(
        "/users",
        json={"name": "Newbie", "email": "newbie@alphaledger.ae", "role": "Staff"},
        headers=admin_headers,
    )
    assert r.status_code == 201
    new_id = r.json()["id"]

    r = client.post(f"/users/{new_id}/deactivate", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["active"] is False
