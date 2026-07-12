from app.models import User
from app.services.auth import hash_password


def make_user(session, username="test", password="testpass"):
    u = User(username=username, password_hash=hash_password(password))
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def test_login_ok(client, session):
    make_user(session)
    resp = client.post("/api/auth/login", json={"username": "test", "password": "testpass"})
    assert resp.status_code == 200
    assert resp.json()["token_type"] == "bearer"
    assert resp.json()["access_token"]


def test_login_wrong_password(client, session):
    make_user(session)
    resp = client.post("/api/auth/login", json={"username": "test", "password": "nope"})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_credentials"


def test_protected_route_requires_token(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401


def test_me_with_token(client, session):
    make_user(session)
    token = client.post(
        "/api/auth/login", json={"username": "test", "password": "testpass"}
    ).json()["access_token"]
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    assert resp.json()["username"] == "test"
