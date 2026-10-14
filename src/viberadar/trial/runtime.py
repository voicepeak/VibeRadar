"""Bounded local Docker execution with persistent lifecycle and revocable keys."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
import subprocess
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlparse

import httpx
from sqlalchemy import select
from viberadar.contracts import new_id, utcnow
from viberadar.contracts.trial import TryRecipe, TryTier
from viberadar.store.models import ProductRow, TrialEventRow, TrialSessionRow, TryRecipeRow
from .recipes import recipe_for_slug

TERMINAL = {"failed", "timeout", "stopped"}
RELAY_IMAGE = "python@sha256:70729b46c69b4f1e97c4822c1af3df53a1476cf5ddc6c087c0c10bc3a5678c2f"


def key_hash(key):
    return hashlib.sha256(key.encode()).hexdigest()


def has_trial_evidence(row, now=None):
    """Ready must have actually lasted 30s, including stopped and expired sessions."""
    if not row.ready_at:
        return False
    end = min(now or utcnow(), row.expires_at, row.finished_at or row.expires_at)
    return (end - row.ready_at).total_seconds() >= 30


def llm_environment(recipe, token):
    values = {}
    for key, placeholder in recipe.llm_env.items():
        if placeholder == "{{virtual_key}}":
            values[key] = token
        else:
            # Anthropic's SDK appends /v1/messages; OpenAI's SDK appends /chat/completions.
            values[key] = "http://relay:3128/llm" if key == "ANTHROPIC_BASE_URL" else "http://relay:3128/llm/v1"
    return values


def workspace(settings, session_id):
    import re
    if not re.fullmatch(r"trl_[a-z0-9]{26}", session_id):
        raise ValueError("Invalid trial id")
    root = (settings.data_dir / "trials").resolve()
    path = (root / session_id).resolve()
    if path.parent != root:
        raise ValueError("Trial workspace escapes data root")
    return path


class Docker:
    @staticmethod
    def executable():
        found = shutil.which("docker")
        if found:
            return found
        if os.name == "nt":
            # A running Web process retains its old PATH after Desktop is installed.
            # Resolve the install record so the environment check can recover immediately.
            import winreg
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, r"Software\Microsoft\Windows\CurrentVersion\Uninstall\Docker Desktop") as key:
                        location = winreg.QueryValueEx(key, "InstallLocation")[0]
                    candidate = Path(location) / "resources" / "bin" / "docker.exe"
                    if candidate.is_file():
                        return str(candidate)
                except OSError:
                    pass
        return "docker"

    def desktop_executable(self):
        if os.name == "nt":
            cli = Path(self.executable())
            candidate = cli.parent.parent.parent / "Docker Desktop.exe"
            if candidate.is_file():
                return candidate
        return None

    def start_desktop(self, timeout=90):
        desktop = self.desktop_executable()
        if not desktop:
            raise RuntimeError("Install Docker Desktop before starting a local trial")
        # Launch via the user's desktop shell so stopping a web/QA worker does not stop
        # the Docker engine. ShowWindow=0 keeps this background launch hidden.
        script = "$radarShell = New-Object -ComObject Shell.Application; $radarShell.ShellExecute('" + str(desktop).replace("'", "''") + "','','','open',0)"
        shell = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        result = subprocess.run([str(shell), "-NoProfile", "-Command", script], capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=30)
        if result.returncode:
            raise RuntimeError("Docker Desktop could not be started; open it from Windows and retry")
        deadline = time.monotonic()+timeout
        while time.monotonic() < deadline:
            if self.available()["available"]:
                return
            time.sleep(2)
        raise TimeoutError("Docker Desktop did not become ready in time")

    def call(self, args, timeout=30):
        # Do not inherit personal tokens, credential stores, Docker contexts or SSH agents.
        env = {k: v for k, v in os.environ.items() if k.upper() in
               {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PROGRAMFILES"}}
        result = subprocess.run([self.executable(), *args], capture_output=True, text=True, encoding="utf-8",
                                errors="replace", timeout=timeout, env=env, shell=False)
        if result.returncode:
            raise RuntimeError((result.stderr or "Docker command failed")[-2000:])
        if args[0] == "logs":
            # Docker keeps the app's stdout/stderr on separate CLI streams even on exit 0.
            return (result.stdout + result.stderr).strip()
        return result.stdout.strip()

    def available(self):
        try:
            return {"available": True, "version": self.call(["version", "--format", "{{.Server.Version}}"])}
        except (OSError, RuntimeError, subprocess.TimeoutExpired):
            return {"available": False, "installed": self.desktop_executable() is not None,
                    "message": "Docker Desktop is unavailable"}


def ensure_image(docker, image, timeout, *, archive_sha256=None, archive_root=None):
    """Only content-addressed images can skip registry checks when already present."""
    import re
    immutable = re.fullmatch(r"(?:[a-zA-Z0-9./:_-]+@)?sha256:[a-f0-9]{64}", image)
    if immutable:
        try:
            docker.call(["image", "inspect", image])
            return
        except RuntimeError as exc:
            if "No such image" not in str(exc):
                raise
        if archive_sha256:
            import re
            if not re.fullmatch(r"[a-f0-9]{64}", archive_sha256) or archive_root is None:
                raise RuntimeError("Invalid prepared image archive reference")
            root = Path(archive_root).resolve()
            archive = (root / (archive_sha256 + ".tar.gz")).resolve()
            if archive.parent != root or not archive.is_file():
                raise RuntimeError("Prepared image archive is unavailable; install the verified recipe pack first")
            if archive.stat().st_size > 512*1024*1024:
                raise RuntimeError("Prepared image archive exceeds the local import limit")
            digest = hashlib.sha256()
            with archive.open("rb") as source:
                while chunk := source.read(1024*1024):
                    digest.update(chunk)
            if digest.hexdigest() != archive_sha256:
                raise RuntimeError("Prepared image archive checksum mismatch")
            docker.call(["load", "--input", str(archive)], timeout=timeout)
            # The archive hash authenticates bytes; the image ID authenticates runnable content.
            docker.call(["image", "inspect", image])
            return
        if image.startswith("sha256:"):
            raise RuntimeError("Prepared local image is missing; prepare it before starting a trial")
    docker.call(["pull", image], timeout=timeout)


def create_trial(factory, settings, slug, actor_hash=None, edition_item_id=None):
    with factory() as s:
        recipe = recipe_for_slug(s, slug)
        if not recipe:
            raise ValueError("No recipe for this product")
        r = TryRecipe(**recipe.content_json)
        if not recipe.verified_at or not r.license_checked or recipe.needs_review:
            raise ValueError("Recipe is unverified or needs review; use the setup guide")
        if r.tier not in (TryTier.CONTAINER, TryTier.COMPOSE):
            raise ValueError("This recipe does not need a local runtime")
        active = list(s.scalars(select(TrialSessionRow).where(TrialSessionRow.status.not_in(TERMINAL))))
        if len(active) >= 3:
            raise ValueError("Stop an existing trial before starting another (limit: 3)")
        same = next((x for x in active if x.recipe_id == recipe.id and x.actor_hash == actor_hash), None)
        if same:
            return same.id, None
        p = s.scalar(select(ProductRow).where(ProductRow.slug == slug))
        token = "vr-trial-" + secrets.token_urlsafe(32)
        now = utcnow()
        row = TrialSessionRow(id=new_id("trl"), recipe_id=recipe.id, product_id=p.id if p else None,
            actor_hash=actor_hash, edition_item_id=edition_item_id, key_hash=key_hash(token),
            budget_usd=r.budget_usd, started_at=now, expires_at=now + timedelta(minutes=r.timeout_minutes))
        s.add(row)
        s.commit()
        return row.id, token


def event(factory, session_id, kind, message, token=None):
    if token:
        message = message.replace(token, "[virtual key]")
    with factory() as s:
        s.add(TrialEventRow(id=new_id("tev"), session_id=session_id, kind=kind,
                           message=message[-8000:], created_at=utcnow()))
        s.commit()


def _state(factory, session_id, status, **fields):
    with factory() as s:
        row = s.get(TrialSessionRow, session_id)
        if row.status in TERMINAL:
            return False
        row.status = status
        for key, value in fields.items():
            setattr(row, key, value)
        s.commit()
    return True


def _constraints(name, net, directory, image, command, *, publish=None, env=None):
    args = ["run", "-d", "--name", name, "--label", "viberadar.trial=true", "--network", net,
        "--user", "1000:1000", "--read-only", "--cap-drop=ALL", "--security-opt", "no-new-privileges",
        "--cpus", "1", "--memory", "512m", "--pids-limit", "128",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=128m", "--mount", f"type=bind,src={directory},dst=/workspace",
        "--workdir", "/workspace", "--entrypoint", "sh", "--env", "HOME=/workspace", "--env", "PYTHONDONTWRITEBYTECODE=1"]
    if publish:
        args += ["-p", f"127.0.0.1::{publish}"]
    for key, value in (env or {}).items():
        args += ["--env", f"{key}={value}"]
    return [*args, image, "-lc", command]


def cleanup_resources(docker, session_id):
    complete = True
    # Fixed names only. Never remove unrelated Docker resources.
    for suffix in ("", "-install", "-relay", "-svc0", "-svc1", "-svc2", "-svc3"):
        try:
            docker.call(["rm", "-f", session_id + suffix])
        except RuntimeError as exc:
            if "No such container" not in str(exc):
                complete = False
        except (OSError, subprocess.TimeoutExpired):
            complete = False
    try:
        docker.call(["network", "rm", session_id + "-net"])
    except RuntimeError as exc:
        if "not found" not in str(exc) and "No such network" not in str(exc):
            complete = False
    except (OSError, subprocess.TimeoutExpired):
        complete = False
    return complete


def execute_trial(factory, settings, session_id, token, docker=None, proxy_port=None):
    docker = docker or Docker()
    with factory() as s:
        row = s.get(TrialSessionRow, session_id)
        recipe_row = s.get(TryRecipeRow, row.recipe_id)
        recipe = TryRecipe(**recipe_row.content_json)
        deadline = row.expires_at
    directory = workspace(settings, session_id)
    directory.mkdir(parents=True, exist_ok=True)
    work = directory / "work"
    work.mkdir(exist_ok=True)
    work.chmod(0o777)
    net = session_id + "-net"
    logs_seen = ""
    try:
        event(factory, session_id, "preparing", "Checking Docker and preparing isolated workspace")
        availability = docker.available()
        if not availability["available"] and availability.get("installed"):
            event(factory, session_id, "preparing", "Starting the installed Docker Desktop engine")
            docker.start_desktop(timeout=min(90, max(1, (deadline-utcnow()).total_seconds())))
            availability = docker.available()
        if not availability["available"]:
            raise RuntimeError("Docker Desktop unavailable. Start Docker Desktop and retry.")
        # Source download is a pinned GitHub tarball, never a host-side install or build.
        if recipe.repo:
            from .source import download_source
            download_source(recipe, work)
        docker.call(["network", "create", "--internal",
                     "--opt", "com.docker.network.bridge.gateway_mode_ipv4=isolated",
                     "--opt", "com.docker.network.bridge.gateway_mode_ipv6=isolated", net])
        policy = directory / "hosts.json"
        policy.write_text(json.dumps(recipe.package_hosts), encoding="utf-8")
        shutil.copyfile(Path(__file__).with_name("relay.py"), directory / "relay.py")
        ensure_image(docker, RELAY_IMAGE, timeout=max(1, (deadline-utcnow()).total_seconds()))
        relay = session_id + "-relay"
        docker.call(["create", "--name", relay, "--label", "viberadar.trial=true", "--network", "bridge",
            "--publish", "127.0.0.1::8080",
            "--add-host", "host.docker.internal:host-gateway", "--user", "1000:1000", "--read-only",
            "--cap-drop=ALL", "--security-opt", "no-new-privileges", "--cpus", "0.25", "--memory", "128m", "--pids-limit", "32",
            "--mount", f"type=bind,src={directory},dst=/policy,readonly", "--env", f"VR_VIRTUAL_KEY={token}",
            "--env", f"VR_APP_HOST={session_id}", "--env", f"VR_APP_PORT={recipe.port}",
            "--env", f"VR_PORT={proxy_port or urlparse(settings.site_base_url).port or 8000}",
            RELAY_IMAGE, "python", "/policy/relay.py"])
        docker.call(["network", "connect", "--alias", "relay", net, relay])
        docker.call(["start", relay])
        proxy_env = {"HTTP_PROXY": "http://relay:3128", "HTTPS_PROXY": "http://relay:3128",
                     "http_proxy": "http://relay:3128", "https_proxy": "http://relay:3128", "NO_PROXY": "relay,localhost,127.0.0.1"}
        llm_env = llm_environment(recipe, token)
        if recipe.image_archive_sha256:
            event(factory, session_id, "preparing", "Checking the verified application image; preparing it if needed")
        ensure_image(docker, recipe.image, timeout=max(1, (deadline-utcnow()).total_seconds()),
                     archive_sha256=recipe.image_archive_sha256,
                     archive_root=settings.data_dir / "recipe-images")
        if not _state(factory, session_id, "installing"):
            return
        if recipe.install:
            event(factory, session_id, "installing", "Installing dependencies through declared package hosts")
            docker.call(_constraints(session_id+"-install", net, work, recipe.image,
                        "set -e\n" + "\n".join(recipe.install), env=proxy_env))
            while True:
                if utcnow() >= deadline:
                    raise TimeoutError("Installation timeout")
                state = json.loads(docker.call(["inspect", "--format", "{{json .State}}", session_id+"-install"]))
                logs = docker.call(["logs", "--tail", "200", session_id+"-install"])
                if logs != logs_seen:
                    event(factory, session_id, "log", logs[len(logs_seen):] if logs.startswith(logs_seen) else logs, token)
                    logs_seen = logs
                if not state["Running"]:
                    if state["ExitCode"]:
                        raise RuntimeError("Dependency installation failed")
                    break
                if not _state(factory, session_id, "installing"):
                    return
                time.sleep(1)
        # Runtime has no external destinations, even if it ignores HTTP_PROXY.
        temporary = directory / "hosts.tmp"
        temporary.write_text("[]", encoding="utf-8")
        temporary.replace(policy)
        for idx, service in enumerate(recipe.services):
            service_dir = work / service.name
            service_dir.mkdir(exist_ok=True)
            service_dir.chmod(0o777)
            ensure_image(docker, service.image, timeout=max(1, (deadline-utcnow()).total_seconds()))
            docker.call(_constraints(session_id+f"-svc{idx}", net, service_dir, service.image,
                        "set -e\n" + "\n".join(service.run), env=service.env))
            # Service names resolve inside the isolated session network.
            docker.call(["network", "disconnect", net, session_id+f"-svc{idx}"])
            docker.call(["network", "connect", "--alias", service.name, net, session_id+f"-svc{idx}"])
        event(factory, session_id, "installing", "Starting application; external network disabled")
        docker.call(_constraints(session_id, net, work, recipe.image, "set -e\n"+"\n".join(recipe.run),
                                 env={**proxy_env, **llm_env}))
        # Internal-only networks do not publish application ports. The fixed-target relay
        # exposes ingress on loopback while the application keeps its isolated network.
        address = docker.call(["port", relay, "8080/tcp"]).splitlines()[0]
        url = "http://" + address
        path = recipe.healthcheck if recipe.healthcheck.startswith("/") else urlparse(recipe.healthcheck).path or "/"
        while utcnow() < deadline:
            if not _state(factory, session_id, "installing"):
                return
            logs = docker.call(["logs", "--tail", "200", session_id])
            if logs != logs_seen:
                event(factory, session_id, "log", logs[len(logs_seen):] if logs.startswith(logs_seen) else logs, token)
                logs_seen = logs
            state = json.loads(docker.call(["inspect", "--format", "{{json .State}}", session_id]))
            if not state["Running"]:
                raise RuntimeError("Application exited before readiness")
            try:
                with httpx.Client(timeout=2, trust_env=False, follow_redirects=False) as client:
                    healthy = client.get(url+path).status_code in range(200, 400)
            except httpx.HTTPError:
                healthy = False
            if healthy:
                now = utcnow()
                if _state(factory, session_id, "ready", url=url, ready_at=now,
                          expires_at=now+timedelta(minutes=recipe.ttl_minutes)):
                    with factory() as s:
                        r = s.get(TryRecipeRow, recipe_row.id)
                        r.consecutive_failures, r.last_success_at = 0, now
                        s.commit()
                    event(factory, session_id, "ready", "Application is ready")
                    return
                return
            time.sleep(1)
        raise TimeoutError("Application readiness timeout")
    except Exception as exc:
        status = "timeout" if isinstance(exc, (TimeoutError, subprocess.TimeoutExpired)) else "failed"
        message = str(exc).replace(token, "[virtual key]")[:2000]
        if _state(factory, session_id, status, error=message, key_hash=None, finished_at=utcnow()):
            with factory() as s:
                r = s.get(TryRecipeRow, recipe_row.id)
                r.consecutive_failures += 1
                if r.consecutive_failures >= 3:
                    r.needs_review = True
                s.commit()
            event(factory, session_id, status, message)
    finally:
        with factory() as s:
            row = s.get(TrialSessionRow, session_id)
            ready = row.status == "ready"
        if not ready:
            cleanup_resources(docker, session_id)


def session_view(s, row, after=None):
    events = list(s.scalars(select(TrialEventRow).where(TrialEventRow.session_id == row.id)
                           .order_by(TrialEventRow.created_at, TrialEventRow.id)))
    if after:
        ids = [e.id for e in events]
        events = events[ids.index(after)+1:] if after in ids else events
    return {"id": row.id, "status": row.status, "url": row.url,
            "evidence_available": has_trial_evidence(row),
            "started_at": row.started_at.isoformat(), "ready_at": row.ready_at.isoformat() if row.ready_at else None,
            "expires_at": row.expires_at.isoformat(), "error": row.error,
            "spent_usd": float(row.spent_usd), "budget_usd": float(row.budget_usd),
            "events": [{"id": e.id, "kind": e.kind, "message": e.message, "at": e.created_at.isoformat()} for e in events]}


def stop_trial(factory, settings, session_id, docker=None, remove=False):
    directory = workspace(settings, session_id)  # validate before any removal
    with factory() as s:
        row = s.get(TrialSessionRow, session_id)
        if not row:
            raise ValueError("Trial does not exist")
        if row.status not in {"failed", "timeout"}:
            row.status = "stopped"
        row.key_hash = None
        row.finished_at = row.finished_at or utcnow()
        row.url = None
        s.commit()
    engine = docker or Docker()
    reclaimed = cleanup_resources(engine, session_id)
    # An unavailable CLI/daemon cannot prove that previously created resources are gone.
    if not reclaimed:
        raise RuntimeError("Docker cleanup is unavailable; the key is revoked and cleanup will retry")
    if remove and directory.exists():
        shutil.rmtree(directory)
    if remove:
        event(factory, session_id, "workspace_reclaimed", "Trial resources and workspace reclaimed")


def clean_trials(factory, settings, docker=None, all_sessions=False):
    with factory() as s:
        rows = list(s.scalars(select(TrialSessionRow)))
        reclaimed = set(s.scalars(select(TrialEventRow.session_id).where(TrialEventRow.kind == "workspace_reclaimed")))
        ids = [r.id for r in rows if r.id not in reclaimed and (all_sessions or r.expires_at <= utcnow() or r.status in TERMINAL)]
    for session_id in ids:
        stop_trial(factory, settings, session_id, docker, remove=True)
    return ids
