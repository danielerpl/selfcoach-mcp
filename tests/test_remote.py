import asyncio
import time

import pytest

from selfcoach_mcp import remote


def test_redirect_hosts():
    ok = ["https://claude.ai/api/mcp/auth_callback", "https://claude.com/x", "http://localhost:3333/cb",
          "http://127.0.0.1:9/cb"]
    bad = ["https://evil.example/cb", "http://claude.ai/cb", "https://claude.ai.evil.com/cb"]
    assert all(remote._host_allowed(u) for u in ok)
    assert not any(remote._host_allowed(u) for u in bad)


def test_wrong_passphrase_locks_out(monkeypatch):
    monkeypatch.setattr(remote, "PASSPHRASE", "correct horse battery")
    p = remote.Provider()
    p.pending["r"] = (time.time() + 60, None, None)
    for _ in range(remote.MAX_FAILURES):
        assert p.complete("r", "nope") is None
    with pytest.raises(remote.AuthorizeError):
        p.complete("r", "correct horse battery")


def test_empty_passphrase_never_matches(monkeypatch):
    monkeypatch.setattr(remote, "PASSPHRASE", "")
    p = remote.Provider()
    p.pending["r"] = (time.time() + 60, None, None)
    assert p.complete("r", "") is None


def test_tokens_rotate_and_revoke():
    p = remote.Provider()
    tok = p._issue("client", ["selfcoach"], None)
    access = asyncio.run(p.load_access_token(tok.access_token))
    assert access and access.client_id == "client"

    class Client:
        client_id = "client"
    refresh = asyncio.run(p.load_refresh_token(Client(), tok.refresh_token))
    new = asyncio.run(p.exchange_refresh_token(Client(), refresh, []))
    assert asyncio.run(p.load_access_token(tok.access_token)) is None  # old pair revoked
    assert asyncio.run(p.load_access_token(new.access_token)) is not None
