"""Real Docker smoke/egress test for a first-party fixture; never claims curated-recipe acceptance.

Run with the local web service active. Uses an isolated database and no real model keys.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from viberadar.contracts import utcnow
from viberadar.contracts.trial import TryRecipe, Verification
from viberadar.settings import PROJECT_ROOT, load_settings
from viberadar.store import get_session_factory
from viberadar.store.models import TrialSessionRow
from viberadar.trial.recipes import register_recipe
from viberadar.trial.runtime import Docker, create_trial, execute_trial, stop_trial, workspace


def main():
    docker = Docker()
    availability = docker.available()
    if not availability["available"]:
        print(json.dumps({"status": "blocked", "reason": "Docker Desktop unavailable",
            "real_container_checks": 0, "curated_recipe_acceptance": "not performed"}))
        return 2
    settings = load_settings(data_dir=PROJECT_ROOT/".local"/"qa-trials")
    factory = get_session_factory(settings.db_path)
    r = TryRecipe(slug="local-python", version="probe-"+utcnow().strftime("%Y%m%d%H%M%S"),
        tier="container", image="python:3.13-slim", run=["python -m http.server 3000 --bind 0.0.0.0"],
        timeout_minutes=2, license_checked="First-party fixture",
        verified=Verification(by="first-party smoke probe", at=utcnow(), device=availability["version"]))
    with factory() as s:
        register_recipe(s, r)
        s.commit()
    session_id, token = create_trial(factory, settings, r.slug)
    class HostProbe(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"viberadar-host-probe")
    host_probe = ThreadingHTTPServer(("127.0.0.1", 0), HostProbe)
    threading.Thread(target=host_probe.serve_forever, daemon=True).start()
    try:
        execute_trial(factory, settings, session_id, token, docker)
        with factory() as s:
            row = s.get(TrialSessionRow, session_id)
            if row.status != "ready":
                raise RuntimeError(row.error or row.status)
            seconds = (row.ready_at-row.started_at).total_seconds()
        inspection = json.loads(docker.call(["inspect", session_id]))[0]
        host = inspection["HostConfig"]
        assert host["ReadonlyRootfs"] and host["Privileged"] is False
        assert inspection["Config"]["User"] == "1000:1000"
        assert host["Memory"] == 512*1024*1024 and host["PidsLimit"] == 128
        assert host["CapDrop"] == ["ALL"]
        assert len(inspection["Mounts"]) == 1 and inspection["Mounts"][0]["Destination"] == "/workspace"
        assert list(inspection["NetworkSettings"]["Networks"]) == [session_id+"-net"]
        network = json.loads(docker.call(["network", "inspect", session_id+"-net"]))[0]
        assert network["Internal"] and network["Options"]["com.docker.network.bridge.gateway_mode_ipv4"] == "isolated"
        # Positive control: the trusted relay can reach a benign host listener. The app cannot.
        host_url = f"http://host.docker.internal:{host_probe.server_port}/"
        host_request = f"import urllib.request; opener=urllib.request.build_opener(urllib.request.ProxyHandler({{}})); assert opener.open('{host_url}',timeout=3).read()==b'viberadar-host-probe'"
        docker.call(["exec", session_id+"-relay", "python", "-c", host_request], timeout=10)
        host_blocked = False
        try:
            docker.call(["exec", session_id, "python", "-c", host_request], timeout=10)
        except RuntimeError:
            host_blocked = True
        assert host_blocked, "App reached a host service outside its session"
        # A malicious app cannot reach the internet even by ignoring proxy environment variables.
        probe = "import socket; socket.create_connection(('1.1.1.1',443),timeout=3)"
        blocked = False
        try:
            docker.call(["exec", session_id, "python", "-c", probe], timeout=10)
        except RuntimeError:
            blocked = True
        assert blocked, "Direct external network was unexpectedly reachable"
        # The package relay must reject runtime tunneling after installation.
        probe = "import urllib.request; urllib.request.urlopen('https://pypi.org',timeout=5)"
        relay_blocked = False
        try:
            docker.call(["exec", session_id, "python", "-c", probe], timeout=10)
        except RuntimeError:
            relay_blocked = True
        assert relay_blocked
        print(json.dumps({"status": "passed", "time_to_ready_seconds": seconds,
            "direct_egress_blocked": blocked, "package_relay_blocked": relay_blocked,
            "host_access_blocked": host_blocked, "relay_host_positive_control": True,
            "curated_recipe_acceptance": "not performed; this is a first-party smoke fixture"}))
        return 0
    finally:
        try:
            stop_trial(factory, settings, session_id, docker, remove=True)
        finally:
            host_probe.shutdown()
            host_probe.server_close()


if __name__ == "__main__":
    sys.exit(main())
