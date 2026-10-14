"""Trial isolation and lifecycle tests use a fake Docker engine; no third-party code runs."""
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import json
import pytest
import httpx
from fastapi.testclient import TestClient
from sqlalchemy import select
from viberadar.contracts import utcnow
from viberadar.contracts.trial import TryRecipe, Verification
from viberadar.llm import LLMConfig, LLMProfile, LLMPrice, load_store
from viberadar.store.models import TrialSessionRow, TryRecipeRow, CostLedgerRow, TrialEventRow
from viberadar.trial.recipes import register_recipe, recipe_label, recipe_stats
from viberadar.trial.runtime import (create_trial, execute_trial, stop_trial, clean_trials, workspace,
                                     _constraints, key_hash, session_view)


def recipe(verified=True, **kwargs):
    return TryRecipe(slug="test-trial", version="v1", tier="container", image="python:3.13-slim",
        run=["python -m http.server 3000"], license_checked="MIT", package_hosts=["pypi.org"],
        llm_env={"OPENAI_API_KEY": "{{virtual_key}}", "OPENAI_BASE_URL": "{{proxy_base}}"},
        verified=Verification(by="test fixture", at=utcnow(), device="fake engine") if verified else None, **kwargs)


def seed(factory, r=None):
    with factory() as s:
        row = register_recipe(s, r or recipe())
        s.commit()
        return row.id


class FakeDocker:
    def __init__(self, available=True, fail=False):
        self.commands = []
        self.online, self.fail = available, fail

    def available(self):
        return {"available": self.online}

    def call(self, args, timeout=30):
        self.commands.append(args)
        if self.fail and args[0] == "pull":
            raise RuntimeError("Pull failed")
        if args[0] == "inspect":
            return json.dumps({"Running": True, "ExitCode": 0})
        if args[0] == "port":
            return "127.0.0.1:54321"
        if args[0] == "logs":
            return "Application starting"
        return "ok"


def test_docker_logs_keep_application_stderr(monkeypatch):
    from types import SimpleNamespace
    from viberadar.trial.runtime import Docker
    monkeypatch.setattr("viberadar.trial.runtime.subprocess.run", lambda *a, **kw:
                        SimpleNamespace(returncode=0, stdout="starting\n", stderr="dependency failed\n"))
    assert Docker().call(["logs", "fixture"]) == "starting\ndependency failed"


@pytest.mark.parametrize("image,present,pulls", [
    ("sha256:"+"a"*64, True, False),
    ("example/app@sha256:"+"a"*64, True, False),
    ("example/app@sha256:"+"a"*64, False, True),
    ("example/app:latest", True, True),
])
def test_only_immutable_cached_images_skip_network(image, present, pulls):
    from viberadar.trial.runtime import ensure_image
    calls = []
    class Engine:
        def call(self, args, timeout=30):
            calls.append(args)
            if args[:2] == ["image", "inspect"] and not present:
                raise RuntimeError("No such image")
            return "ok"
    ensure_image(Engine(), image, timeout=60)
    assert (["pull", image] in calls) is pulls


def test_missing_prepared_image_requires_preparation():
    from viberadar.trial.runtime import ensure_image
    class Engine:
        def call(self, args, timeout=30):
            assert args[:2] == ["image", "inspect"]
            raise RuntimeError("No such image")
    with pytest.raises(RuntimeError, match="prepare"):
        ensure_image(Engine(), "sha256:"+"a"*64, timeout=60)


def test_prepared_archive_is_checked_then_imported(tmp_path):
    import hashlib
    from viberadar.trial.runtime import ensure_image
    data = b"trusted test archive"
    digest = hashlib.sha256(data).hexdigest()
    (tmp_path / (digest+".tar.gz")).write_bytes(data)
    calls = []
    class Engine:
        loaded = False
        def call(self, args, timeout=30):
            calls.append(args)
            if args[0] == "load": self.loaded = True
            elif not self.loaded: raise RuntimeError("No such image")
            return "ok"
    ensure_image(Engine(), "sha256:"+"a"*64, 60, archive_sha256=digest, archive_root=tmp_path)
    assert [x[0] for x in calls] == ["image", "load", "image"]


def test_prepared_archive_mismatch_never_imported(tmp_path):
    from viberadar.trial.runtime import ensure_image
    digest = "b"*64
    (tmp_path / (digest+".tar.gz")).write_bytes(b"tampered")
    class Engine:
        def call(self, args, timeout=30):
            assert args[:2] == ["image", "inspect"]
            raise RuntimeError("No such image")
    with pytest.raises(RuntimeError, match="checksum"):
        ensure_image(Engine(), "sha256:"+"a"*64, 60, archive_sha256=digest, archive_root=tmp_path)


@pytest.mark.parametrize("address,expected", [("198.18.0.163", "151.101.0.223"),
                                              ("192.168.1.1", None), ("151.101.0.223", "151.101.0.223")])
def test_fake_ip_dns_never_connects_to_private_address(monkeypatch, address, expected):
    from viberadar.trial import relay
    relay.public_address.cache_clear()
    monkeypatch.setattr(relay.socket, "getaddrinfo", lambda *a, **kw: [(2, 1, 6, "", (address, 443))])
    resolved = []
    monkeypatch.setattr(relay, "public_dns_address", lambda h: resolved.append(h) or "151.101.0.223")
    if expected is None:
        with pytest.raises(ValueError, match="Private"):
            relay.public_address("pypi.org")
    else:
        assert relay.public_address("pypi.org") == expected
    assert resolved == (["pypi.org"] if address.startswith("198.18.") else [])
    relay.public_address.cache_clear()


def test_app_ingress_streams_to_fixed_app_and_rejects_absolute_targets(monkeypatch):
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    import threading
    from viberadar.trial.relay import AppIngress
    seen = []
    class App(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_POST(self):
            seen.append((self.path, self.rfile.read(int(self.headers["Content-Length"]))))
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            self.wfile.write(b"data: app-only\n\n")
    app_server = ThreadingHTTPServer(("127.0.0.1", 0), App)
    ingress = ThreadingHTTPServer(("127.0.0.1", 0), AppIngress)
    monkeypatch.setenv("VR_APP_HOST", "127.0.0.1")
    monkeypatch.setenv("VR_APP_PORT", str(app_server.server_port))
    for server in (app_server, ingress):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{ingress.server_port}"
        response = httpx.post(url+"/convert", content=b"sample", headers={"Host": "attacker.test"}, trust_env=False)
        assert response.status_code == 200 and response.headers["content-type"] == "text/event-stream"
        assert response.text == "data: app-only\n\n" and seen == [("/convert", b"sample")]
        # A caller-selected URL cannot turn ingress into an outbound HTTP proxy.
        import http.client
        connection = http.client.HTTPConnection("127.0.0.1", ingress.server_port)
        connection.request("GET", "http://169.254.169.254/")
        assert connection.getresponse().status == 400
        connection.close()
    finally:
        for server in (ingress, app_server):
            server.shutdown()
            server.server_close()


@pytest.mark.parametrize("address,allowed", [("127.0.0.1", False), ("198.18.0.1", False), ("151.101.0.223", True)])
def test_public_dns_fallback_validates_response_addresses(monkeypatch, address, allowed):
    from viberadar.trial import relay
    class Response:
        status = 200
        def read(self, limit):
            return json.dumps({"Status": 0, "Answer": [{"type": 1, "data": address}]}).encode()
    class Connection:
        def __init__(self, host, timeout): assert host == "cloudflare-dns.com"
        def request(self, method, path, headers): assert method == "GET" and path.startswith("/dns-query?")
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr(relay.http.client, "HTTPSConnection", Connection)
    if allowed:
        assert relay.public_dns_address("pypi.org") == address
    else:
        with pytest.raises(ValueError, match="Private"):
            relay.public_dns_address("pypi.org")


def test_app_ingress_preserves_websocket_handshake_and_frames(monkeypatch):
    import base64, hashlib, socket, threading
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from viberadar.trial.relay import AppIngress
    class WebsocketApp(BaseHTTPRequestHandler):
        def log_message(self, *a): pass
        def do_GET(self):
            accept = base64.b64encode(hashlib.sha1((self.headers["Sec-WebSocket-Key"]+
                "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode()).digest()).decode()
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.send_header("Sec-WebSocket-Accept", accept)
            self.end_headers()
            opcode, length = self.rfile.read(2)
            mask = self.rfile.read(4)
            data = self.rfile.read(length & 127)
            decoded = bytes(value ^ mask[i % 4] for i, value in enumerate(data))
            self.wfile.write(bytes([opcode, len(decoded)])+decoded)
            self.wfile.flush()
    app_server = ThreadingHTTPServer(("127.0.0.1", 0), WebsocketApp)
    ingress = ThreadingHTTPServer(("127.0.0.1", 0), AppIngress)
    monkeypatch.setenv("VR_APP_HOST", "127.0.0.1")
    monkeypatch.setenv("VR_APP_PORT", str(app_server.server_port))
    for server in (app_server, ingress):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        with socket.create_connection(("127.0.0.1", ingress.server_port), timeout=5) as client:
            client.sendall(b"GET /ws HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\n\r\n")
            response = b""
            while not response.endswith(b"\r\n\r\n"):
                response += client.recv(1)
            assert b"101" in response and b"s3pPLMBiTxaQ9kYGzzhZRbK+xOo=" in response
            mask, payload = b"abcd", b"ping"
            client.sendall(b"\x81\x84"+mask+bytes(value ^ mask[i % 4] for i, value in enumerate(payload)))
            frame = b""
            while len(frame) < 6:
                frame += client.recv(6-len(frame))
            assert frame == b"\x81\x04ping"
    finally:
        for server in (ingress, app_server):
            server.shutdown()
            server.server_close()


def test_recipe_freeze_and_unverified_downgrade(session_factory, temp_settings):
    seed(session_factory, recipe(False))
    with pytest.raises(ValueError, match="unverified"):
        create_trial(session_factory, temp_settings, "test-trial")
    with session_factory() as s:
        row = s.scalar(select(TryRecipeRow))
        assert recipe_label(row)["tier"] == "guided"
        changed = recipe(False)
        changed.port = 4000
        with pytest.raises(ValueError, match="immutable"):
            register_recipe(s, changed)


def test_discovered_repo_slug_matches_recipe_by_canonical_identity(session_factory, temp_settings):
    from viberadar.store import repo_products
    from viberadar.trial.recipes import recipe_for_slug
    candidate = recipe(entry_url="https://github.com/microsoft/markitdown")
    with session_factory() as session:
        register_recipe(session, candidate)
        discovered = repo_products.create_product(session, "microsoft/markitdown",
            "https://github.com/microsoft/markitdown", None, "github:microsoft/markitdown", utcnow())
        other = repo_products.create_product(session, "different/markitdown",
            "https://github.com/different/markitdown", None, "github:different/markitdown", utcnow())
        assert recipe_for_slug(session, discovered.slug).slug == "test-trial"
        assert recipe_for_slug(session, other.slug) is None
        session.commit()
    session_id, _ = create_trial(session_factory, temp_settings, discovered.slug)
    with session_factory() as session:
        assert session.get(TrialSessionRow, session_id).product_id == discovered.id


def test_recipe_no_raw_keys_pinned_sources_and_host_mounts():
    with pytest.raises(ValueError):
        recipe().model_validate({**recipe().model_dump(), "llm_env": {"OPENAI_API_KEY": "sk-real"}})
    with pytest.raises(ValueError):
        recipe().model_validate({**recipe().model_dump(), "volumes": ["C:/:/host"]})
    with pytest.raises(ValueError):
        recipe().model_validate({**recipe().model_dump(), "repo": "https://github.com/a/b"})
    with pytest.raises(ValueError):
        recipe().model_validate({**recipe().model_dump(), "healthcheck": "http://169.254.169.254/"})


def test_isolation_commands_and_virtual_key_only(session_factory, temp_settings, monkeypatch):
    seed(session_factory)
    session_id, token = create_trial(session_factory, temp_settings, "test-trial")
    fake = FakeDocker()
    monkeypatch.setattr("viberadar.trial.runtime.httpx.Client.get", lambda self, url: httpx.Response(200))
    execute_trial(session_factory, temp_settings, session_id, token, fake)
    with session_factory() as s:
        row = s.get(TrialSessionRow, session_id)
        assert row.status == "ready", row.error
        assert row.key_hash == key_hash(token) and row.url == "http://127.0.0.1:54321"
        assert row.expires_at - row.ready_at == timedelta(hours=2)
        assert recipe_stats(s, s.scalar(select(TryRecipeRow)))["median_ready_seconds"] is not None
        assert all(token not in e.message for e in s.scalars(select(TrialEventRow)))
    main = next(c for c in fake.commands if c[0] == "run" and c[c.index("--name")+1] == session_id)
    assert "--read-only" in main and "--cap-drop=ALL" in main and "1000:1000" in main
    assert "--memory" in main and "--pids-limit" in main and "--cpus" in main
    assert not any("docker.sock" in arg for arg in main)
    assert main[main.index("--network")+1] == session_id+"-net"
    assert "-p" not in main and "--publish" not in main
    ingress = next(c for c in fake.commands if c[0] == "create")
    assert ingress[ingress.index("--publish")+1] == "127.0.0.1::8080"
    assert f"VR_APP_HOST={session_id}" in ingress
    assert ["port", session_id+"-relay", "8080/tcp"] in fake.commands
    network = next(c for c in fake.commands if c[:2] == ["network", "create"])
    assert "com.docker.network.bridge.gateway_mode_ipv4=isolated" in network
    assert (workspace(temp_settings, session_id)/"hosts.json").read_text() == "[]"
    assert any("OPENAI_API_KEY=vr-trial-" in arg for arg in main)
    stop_trial(session_factory, temp_settings, session_id, fake)
    with session_factory() as s:
        assert s.get(TrialSessionRow, session_id).key_hash is None
        assert s.get(TrialSessionRow, session_id).status == "stopped"
    assert ["network", "rm", session_id+"-net"] in fake.commands


def test_launch_starts_an_installed_stopped_engine(session_factory, temp_settings, monkeypatch):
    seed(session_factory)
    session_id, token = create_trial(session_factory, temp_settings, "test-trial")
    class StoppedDesktop(FakeDocker):
        started = False
        def available(self): return {"available": self.started, "installed": True}
        def start_desktop(self, timeout): self.started = True
    engine = StoppedDesktop()
    monkeypatch.setattr("viberadar.trial.runtime.httpx.Client.get", lambda self, url: httpx.Response(200))
    execute_trial(session_factory, temp_settings, session_id, token, engine)
    assert engine.started
    with session_factory() as session:
        assert session.get(TrialSessionRow, session_id).status == "ready"


def test_failures_demote_and_reclaim(session_factory, temp_settings):
    seed(session_factory)
    for _ in range(3):
        session_id, token = create_trial(session_factory, temp_settings, "test-trial")
        execute_trial(session_factory, temp_settings, session_id, token, FakeDocker(available=False))
    with session_factory() as s:
        row = s.scalar(select(TryRecipeRow))
        assert row.needs_review and row.consecutive_failures == 3
        assert recipe_label(row)["tier"] == "guided"
    cleaned = clean_trials(session_factory, temp_settings, FakeDocker())
    assert len(cleaned) == 3
    assert not any(workspace(temp_settings, x).exists() for x in cleaned)
    with pytest.raises(ValueError):
        workspace(temp_settings, "../../outside")


def test_expiry_and_preparing_crash_recovery(session_factory, temp_settings):
    seed(session_factory)
    session_id, _ = create_trial(session_factory, temp_settings, "test-trial")
    with session_factory() as s:
        row = s.get(TrialSessionRow, session_id)
        row.expires_at = utcnow()-timedelta(seconds=1)
        s.commit()
    assert clean_trials(session_factory, temp_settings, FakeDocker()) == [session_id]
    with session_factory() as s:
        assert s.get(TrialSessionRow, session_id).key_hash is None


def test_trial_web_local_auth_no_js_and_feedback(app, temp_settings, monkeypatch):
    monkeypatch.setattr("viberadar.trial.runtime.Docker.available", lambda self: {"available": False})
    factory = app.state.session_factory
    seed(factory)
    with TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1", 5000), headers={"Origin": "http://127.0.0.1"}) as c:
        page = c.get("/try/test-trial")
        assert page.status_code == 200
        assert "准备时间未验证" in page.text and "Docker Desktop" in page.text
        assert c.post("/api/v1/trials/test-trial", headers={"Origin": "http://evil.test"}).status_code == 403
        response = c.post("/try/test-trial", follow_redirects=False)
        assert response.status_code == 303
        location = response.headers["location"]
        trial_page = c.get(location)
        assert trial_page.status_code == 200 and "trial-logs" in trial_page.text
        session_id = location.split("/")[-1]
        status = c.get(f"/api/v1/trial-sessions/{session_id}").json()
        assert status["status"] == "failed"  # no Docker installed in this test environment
        assert "key_hash" not in status
        assert c.post(f"/api/v1/trial-sessions/{session_id}/tried").status_code == 409
        assert c.get(f"/api/v1/trial-sessions/{session_id}/logs").headers["content-type"].startswith("text/event-stream")
    with TestClient(app, base_url="http://attacker.test", client=("127.0.0.1", 5000), headers={"Origin": "http://attacker.test"}) as c:
        assert c.post("/api/v1/trials/test-trial").status_code == 403  # DNS rebinding


def test_proxy_protocols_budget_and_revocation(app, temp_settings, monkeypatch):
    monkeypatch.setenv("VIBERADAR_LLM_PROVIDER", "config")
    store = load_store(temp_settings)
    store.save(LLMConfig(active="test", profiles={"test": LLMProfile(name="test", api_key="sk-real-test-secret",
        base_url="https://model.test/v1", model_fast="fast-test", model_strong="strong-test",
        price=LLMPrice(input=1, output=2))}))
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}],
              "usage": {"prompt_tokens": 10, "completion_tokens": 10}})
    app.state.llm_transport = httpx.MockTransport(handler)
    factory = app.state.session_factory
    seed(factory)
    session_id, token = create_trial(factory, temp_settings, "test-trial")
    with factory() as s:
        row = s.get(TrialSessionRow, session_id)
        row.status = "ready"
        s.commit()
    with TestClient(app) as c:
        payload = {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 20}
        assert c.post("/api/trial-proxy/chat/completions", json=payload).status_code == 401
        headers = {"Authorization": "Bearer " + token}
        response = c.post("/api/trial-proxy/chat/completions", json=payload, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()["choices"][0]["message"]["content"] == "OK"
        assert token not in response.text and "sk-real" not in response.text
        assert seen[0].headers["authorization"] == "Bearer sk-real-test-secret"
        assert json.loads(seen[0].content)["model"] == "fast-test"
        anthropic = c.post("/api/trial-proxy/messages", json=payload, headers=headers)
        assert anthropic.json()["content"][0]["text"] == "OK"
        streamed = c.post("/api/trial-proxy/chat/completions", json={**payload, "stream": True}, headers=headers)
        assert "text/event-stream" in streamed.headers["content-type"] and "[DONE]" in streamed.text
        assert c.post("/api/trial-proxy/chat/completions", json={**payload, "messages": [{"role": "user", "content": ["bad"]}]}, headers=headers).status_code == 400
        with factory() as s:
            row = s.get(TrialSessionRow, session_id)
            assert row.spent_usd > 0
            row.spent_usd = row.budget_usd
            s.commit()
        assert c.post("/api/trial-proxy/chat/completions", json=payload, headers=headers).status_code == 429
        assert len(seen) == 3
        stop_trial(factory, temp_settings, session_id, FakeDocker())
        assert c.post("/api/trial-proxy/chat/completions", json=payload, headers=headers).status_code == 401


def test_reclaimed_failures_remain_failures_in_stats(session_factory, temp_settings):
    seed(session_factory)
    session_id, token = create_trial(session_factory, temp_settings, "test-trial")
    execute_trial(session_factory, temp_settings, session_id, token, FakeDocker(available=False))
    clean_trials(session_factory, temp_settings, FakeDocker())
    with session_factory() as s:
        stats = recipe_stats(s, s.scalar(select(TryRecipeRow)))
        assert stats["success_rate"] == 0.0
        assert s.get(TrialSessionRow, session_id).status == "failed"
        s.get(TrialSessionRow,session_id).status="stopped"  # Old cleanup record remains countable.
        s.flush()
        assert recipe_stats(s,s.scalar(select(TryRecipeRow)))["success_rate"] == 0.0


def test_tried_rejects_a_session_stopped_before_thirty_seconds(app, temp_settings):
    factory = app.state.session_factory
    seed(factory)
    session_id, _ = create_trial(factory, temp_settings, "test-trial")
    now = utcnow()
    with factory() as s:
        row = s.get(TrialSessionRow, session_id)
        row.status, row.ready_at, row.finished_at = "stopped", now-timedelta(seconds=60), now-timedelta(seconds=59)
        s.commit()
    # No lifespan: verify historical evidence without the periodic janitor changing the fixture.
    c = TestClient(app, base_url="http://127.0.0.1", client=("127.0.0.1",5000), headers={"Origin":"http://127.0.0.1"})
    assert c.post(f"/api/v1/trial-sessions/{session_id}/tried").status_code == 409


def test_relay_preserves_sse_and_checks_client_key(monkeypatch):
    import threading
    from http.server import ThreadingHTTPServer
    from viberadar.trial.relay import Relay
    class Upstream:
        status = 200
        def getheader(self, name, default=None):
            return "text/event-stream" if name.lower() == "content-type" else default
        def read(self, limit):
            return b'data: {"delta":"ok"}\n\ndata: [DONE]\n\n'
    class Connection:
        def __init__(self, *args, **kwargs): pass
        def request(self, *args, **kwargs): pass
        def getresponse(self): return Upstream()
        def close(self): pass
    monkeypatch.setenv("VR_PORT", "8000")
    monkeypatch.setenv("VR_VIRTUAL_KEY", "vr-trial-fixture")
    monkeypatch.setattr("viberadar.trial.relay.http.client.HTTPConnection", Connection)
    server = ThreadingHTTPServer(("127.0.0.1",0), Relay)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{server.server_port}", trust_env=False) as c:
            response = c.post("/llm/v1/chat/completions", json={"messages":[]}, headers={"Authorization":"Bearer vr-trial-fixture"})
            assert response.headers["content-type"].startswith("text/event-stream")
            assert "[DONE]" in response.text
            assert c.post("/llm/v1/chat/completions", json={"messages":[]}).status_code == 401
            assert c.post("/llm/v1/chat/completions", json={"messages":[]}, headers={"Authorization":"Bearer wrong"}).status_code == 401
            assert c.post("/llm/v1/messages", json={"messages":[]}, headers={"x-api-key":"vr-trial-fixture"}).status_code == 200
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_cleanup_failure_does_not_claim_resources_reclaimed(session_factory,temp_settings):
    class UnavailableDocker(FakeDocker):
        def call(self,*args,**kwargs):
            raise RuntimeError("Docker daemon unavailable")
    seed(session_factory)
    session_id,_ = create_trial(session_factory,temp_settings,"test-trial")
    directory=workspace(temp_settings,session_id)
    directory.mkdir(parents=True)
    with pytest.raises(RuntimeError,match="cleanup"):
        stop_trial(session_factory,temp_settings,session_id,UnavailableDocker(),remove=True)
    assert directory.exists()
    with session_factory() as s:
        assert s.get(TrialSessionRow,session_id).key_hash is None
        assert not s.scalar(select(TrialEventRow).where(TrialEventRow.session_id==session_id,TrialEventRow.kind=="workspace_reclaimed"))


def test_anthropic_injection_uses_sdk_base_path():
    from viberadar.trial.runtime import llm_environment
    r=recipe()
    r.llm_env={"ANTHROPIC_BASE_URL":"{{proxy_base}}","ANTHROPIC_API_KEY":"{{virtual_key}}"}
    env=llm_environment(r,"vr-trial-fixture")
    assert env["ANTHROPIC_BASE_URL"]+"/v1/messages" == "http://relay:3128/llm/v1/messages"
    assert env["ANTHROPIC_API_KEY"] == "vr-trial-fixture"


def test_guided_trial_language_purity(app):
    r=recipe(False)
    r.guide=["打开项目并安装依赖"]
    r.guide_en=["Open the official project and follow its setup guide."]
    seed(app.state.session_factory,r)
    c=TestClient(app,base_url="http://127.0.0.1",client=("127.0.0.1",5000))
    zh=c.get("/try/test-trial?lang=zh")
    en=c.get("/try/test-trial?lang=en")
    assert "打开项目并安装依赖" in zh.text
    assert "Open the official project" in en.text and "打开项目并安装依赖" not in en.text
