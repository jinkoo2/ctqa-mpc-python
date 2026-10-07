def test_users_dir_defaults_next_to_exe(tmp_path, monkeypatch):
    from ctqa_mpc.identity import ENV_USERS, USERS_DIRNAME, users_dir

    monkeypatch.delenv(ENV_USERS, raising=False)
    monkeypatch.delenv("CTQA_MPC_SETTINGS", raising=False)
    monkeypatch.setattr("ctqa_mpc.identity.app_dir", lambda: tmp_path)
    assert users_dir() == tmp_path / USERS_DIRNAME
    override = tmp_path / "shared"
    monkeypatch.setenv(ENV_USERS, str(override))
    assert users_dir() == override.resolve()


def test_identity_defaults_and_os_user(tmp_path, monkeypatch):
    from ctqa_mpc.app_settings import save_settings
    from ctqa_mpc import identity as ident
    from ctqa_mpc.identity import (
        USER_ID_NONE,
        USER_ID_OSUSER,
        collect_os_user,
        current_user_label,
        get_user_id_method,
        os_user_id,
        upsert_os_user_profile,
    )

    ident._os_facts_cache = None
    monkeypatch.setenv("CTQA_MPC_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CTQA_MPC_USERS_DIR", str(tmp_path / "_users"))

    assert get_user_id_method({}) == USER_ID_OSUSER
    assert get_user_id_method({"Identity": {"user_id_method": "None"}}) == USER_ID_NONE
    assert get_user_id_method({"Identity": {"user_id_method": "osuser"}}) == USER_ID_OSUSER

    facts = collect_os_user()
    assert facts.get("username")
    assert facts.get("hostname")
    assert facts.get("os") in ("Windows", "Linux", "Darwin")
    assert os_user_id(facts).startswith("osuser:")

    save_settings({"Identity": {"user_id_method": "OSUser"}})
    ident._os_facts_cache = None
    profile = upsert_os_user_profile()
    assert profile["method"] == "OSUser"
    assert profile["id"] == os_user_id()
    assert "subscriptions" in profile
    assert (tmp_path / "_users").is_dir()
    assert current_user_label() == profile["display_name"]

    from ctqa_mpc.identity import coerce_registration_url, oidc_settings

    cfg = oidc_settings({})
    assert cfg["issuer"] == "https://idp.example.edu/realms/example"
    assert cfg["client_id"] == "ctqa-mpc"
    assert cfg["scopes"] == "openid profile email"
    assert cfg["registration_url"].endswith("/realms/example/account/")
    assert cfg["redirect_uri"] == "http://127.0.0.1:17844/callback"

    filled = oidc_settings(
        {
            "Identity": {
                "oidc": {
                    "keycloak_url": "https://idp.example.edu",
                    "keycloak_realm": "example",
                }
            }
        }
    )
    assert filled["issuer"] == "https://idp.example.edu/realms/example"

    broken = (
        "https://idp.example.edu/realms/example/protocol/openid-connect/registrations"
        "?client_id=account-console&response_type=code&scope=openid"
        "&redirect_uri=https%3A%2F%2Fidp.example.edu%2Frealms%2Fexample%2Faccount%2F"
    )
    rewritten = oidc_settings({"Identity": {"oidc": {"registration_url": broken}}})
    assert rewritten["registration_url"].endswith("/realms/example/account/")
    assert coerce_registration_url(broken).endswith("/account/")

    from ctqa_mpc.oidc import authorization_url, decode_jwt_payload, pkce_pair

    verifier, challenge = pkce_pair()
    assert verifier and challenge and verifier != challenge
    payload = {"sub": "abc", "name": "Pat", "email": "pat@example.edu"}
    import base64
    import json as jsonlib

    body = base64.urlsafe_b64encode(jsonlib.dumps(payload).encode()).rstrip(b"=").decode()
    claims = decode_jwt_payload(f"hdr.{body}.sig")
    assert claims["sub"] == "abc"
    url = authorization_url(
        cfg,
        redirect_uri=cfg["redirect_uri"],
        state="st",
        code_challenge=challenge,
        authorization_endpoint="https://idp.example.edu/realms/example/protocol/openid-connect/auth",
    )
    assert "code_challenge_method=S256" in url
    assert "client_id=ctqa-mpc" in url


def test_user_profile_machines_and_subscribers(tmp_path, monkeypatch):
    from ctqa_mpc import identity as ident
    from ctqa_mpc.identity import (
        NOTIFY_NEW_CASE_ALL_MACHINES,
        NOTIFY_NEW_CASE_MY_MACHINES,
        looks_like_email,
        save_user_profile,
        subscribers_for_new_qa_case,
        upsert_os_user_profile,
        user_needs_email,
    )

    ident._os_facts_cache = None
    monkeypatch.setenv("CTQA_MPC_SETTINGS", str(tmp_path / "settings.json"))
    monkeypatch.setenv("CTQA_MPC_USERS_DIR", str(tmp_path / "_users"))

    assert looks_like_email("pat@hospital.edu")
    assert not looks_like_email("not-an-email")

    profile = upsert_os_user_profile()
    profile["email"] = ""
    profile["my_machines"] = ["CTSim1"]
    profile["subscriptions"] = {
        "new_qa_case": NOTIFY_NEW_CASE_MY_MACHINES,
        "email": False,
        "google_chat": False,
        "slack": False,
        "microsoft_teams": False,
        "discord": False,
    }
    save_user_profile(profile)
    assert subscribers_for_new_qa_case("CTSim1") == []

    profile["email"] = "pat@hospital.edu"
    save_user_profile(profile)
    mine = subscribers_for_new_qa_case("CTSim1")
    assert len(mine) == 1
    assert mine[0]["email"] == "pat@hospital.edu"
    assert subscribers_for_new_qa_case("OtherCT") == []

    profile["subscriptions"]["new_qa_case"] = NOTIFY_NEW_CASE_ALL_MACHINES
    save_user_profile(profile)
    assert subscribers_for_new_qa_case("OtherCT")[0]["email"] == "pat@hospital.edu"

    from ctqa_mpc.app_settings import save_settings

    save_settings({"Identity": {"user_id_method": "OSUser"}})
    profile["email"] = ""
    save_user_profile(profile)
    assert user_needs_email() is True
