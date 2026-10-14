from __future__ import annotations

import json
from decimal import Decimal
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from viberadar.contracts import utcnow
from viberadar.contracts.trial import TryRecipe
from viberadar.feedback import limiter
from viberadar.llm import make_gateway
from viberadar.llm.costs import compute_cost
from viberadar.store import repo_products
from viberadar.store.models import TrialSessionRow, CostLedgerRow
from viberadar.trial.recipes import recipe_for_slug, recipe_label, recipe_stats
from viberadar.trial.runtime import (Docker, create_trial, execute_trial, key_hash, session_view, stop_trial, event, has_trial_evidence)
from .deps import ensure_cookie, get_session, resolve_actor, resolve_locale, same_origin
from .routes_llm import _trusted_local
from .templating import render

router = APIRouter()


def local_only(request, write=False):
    if not _trusted_local(request) or request.url.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise HTTPException(403, "Trials are available on the local machine only")
    if write and not same_origin(request):
        raise HTTPException(403, "Same-origin request required")


def owned(request, row):
    local_only(request)
    if not row:
        raise HTTPException(404)
    actor = resolve_actor(request, request.app.state.settings)
    if row.actor_hash and row.actor_hash != actor.actor_hash:
        raise HTTPException(403)


@router.get("/try/{slug}")
def try_page(slug: str, request: Request, session: Session = Depends(get_session)):
    locale = resolve_locale(request)
    recipe = recipe_for_slug(session, slug)
    if not recipe:
        raise HTTPException(404)
    product = repo_products.get_product_by_slug(session, slug)
    r = TryRecipe(**recipe.content_json)
    response = render(request, "try.html", {"settings": request.app.state.settings,
        "slug": slug, "name": product.name if product else slug, "recipe": r,
        "trial_label": recipe_label(recipe, locale), "stats": recipe_stats(session, recipe),
        "runtime": Docker().available(), "local": _trusted_local(request) and request.url.hostname in ("localhost", "127.0.0.1", "::1")})
    ensure_cookie(request, response, request.app.state.settings,
                  resolve_actor(request, request.app.state.settings, create=True))
    return response


def _launch(slug, request, tasks):
    local_only(request, write=True)
    if not limiter.allow("trial:" + (request.client.host if request.client else ""), 6):
        raise HTTPException(429)
    settings = request.app.state.settings
    actor = resolve_actor(request, settings, create=True)
    try:
        session_id, token = create_trial(request.app.state.session_factory, settings, slug, actor.actor_hash)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if token:
        tasks.add_task(execute_trial, request.app.state.session_factory, settings, session_id, token,
                       proxy_port=request.url.port or 80)
    response = JSONResponse({"session_id": session_id}, status_code=202)
    ensure_cookie(request, response, settings, actor)
    return response


@router.post("/api/v1/trials/{slug}")
def launch(slug: str, request: Request, tasks: BackgroundTasks):
    return _launch(slug, request, tasks)


@router.post("/try/{slug}")
def launch_form(slug: str, request: Request, tasks: BackgroundTasks):
    result = _launch(slug, request, tasks)
    session_id = json.loads(result.body)["session_id"]
    response = RedirectResponse(f"/trial/{session_id}", status_code=303)
    for key, value in result.raw_headers:
        if key == b"set-cookie":
            response.raw_headers.append((key, value))
    return response


@router.get("/trial/{session_id}")
def trial_page(session_id: str, request: Request, session: Session = Depends(get_session)):
    row = session.get(TrialSessionRow, session_id)
    owned(request, row)
    return render(request, "trial.html", {"settings": request.app.state.settings, "trial": session_view(session, row)})


@router.get("/api/v1/trial-sessions/{session_id}")
def status(session_id: str, request: Request, after: str | None = None, session: Session = Depends(get_session)):
    row = session.get(TrialSessionRow, session_id)
    owned(request, row)
    return JSONResponse(session_view(session, row, after), headers={"Cache-Control": "no-store"})


@router.get("/api/v1/trial-sessions/{session_id}/logs")
def logs(session_id: str, request: Request, after: str | None = None, session: Session = Depends(get_session)):
    row = session.get(TrialSessionRow, session_id)
    owned(request, row)
    # Short SSE batches reconnect with the last event id; no long-lived SQLite transaction.
    after = after or request.headers.get("last-event-id")
    view = session_view(session, row, after)
    body = "retry: 2000\n\n" + "".join(f"id: {e['id']}\ndata: {json.dumps(e)}\n\n" for e in view["events"])
    body += f"event: status\ndata: {json.dumps({'status': row.status})}\n\n"
    return StreamingResponse(iter([body]), media_type="text/event-stream", headers={"Cache-Control": "no-store"})


@router.post("/api/v1/trial-sessions/{session_id}/stop")
@router.post("/trial/{session_id}/stop")
def stop(session_id: str, request: Request, session: Session = Depends(get_session)):
    local_only(request, write=True)
    row = session.get(TrialSessionRow, session_id)
    owned(request, row)
    session.commit()
    stop_trial(request.app.state.session_factory, request.app.state.settings, session_id)
    return {"status": "stopped"} if request.url.path.startswith("/api/") else RedirectResponse(f"/trial/{session_id}", status_code=303)


@router.post("/api/v1/trial-sessions/{session_id}/tried")
def tried(session_id: str, request: Request, session: Session = Depends(get_session)):
    local_only(request, write=True)
    row = session.get(TrialSessionRow, session_id)
    owned(request, row)
    if not has_trial_evidence(row):
        raise HTTPException(409, "Confirm after experiencing the ready application")
    actor = resolve_actor(request, request.app.state.settings)
    from viberadar.store.models import TrialEventRow
    existing = session.scalar(select(TrialEventRow).where(TrialEventRow.session_id == session_id,
                              TrialEventRow.kind == "tried", TrialEventRow.actor_hash == actor.actor_hash))
    if not existing:
        from viberadar.contracts import new_id
        session.add(TrialEventRow(id=new_id("tev"), session_id=session_id, kind="tried",
            message="Reader confirmed trying the application after readiness", actor_hash=actor.actor_hash, created_at=utcnow()))
    return {"ok": True, "trial_session_id": session_id, "evidence": "manual_confirmation_after_ready"}


@router.post("/api/trial-proxy/chat/completions")
@router.post("/api/trial-proxy/messages")
async def proxy(request: Request):
    token = request.headers.get("authorization", "").removeprefix("Bearer ") or request.headers.get("x-api-key", "")
    if not token:
        raise HTTPException(401)
    if not limiter.allow("trial-proxy:" + key_hash(token), 30):
        raise HTTPException(429)
    if len(await request.body()) > 500_000:
        raise HTTPException(413)
    try:
        body = await request.json()
        if not isinstance(body, dict) or not isinstance(body.get("messages"), list) or not body["messages"]:
            raise ValueError()
        if body.get("tools"):
            raise ValueError("Tool calls are not supported by the trial proxy")
        max_tokens = min(max(int(body.get("max_tokens") or body.get("max_completion_tokens") or 1600), 1), 8000)
    except (ValueError, TypeError):
        raise HTTPException(400, "Invalid messages payload; only text conversations are supported")
    from starlette.concurrency import run_in_threadpool
    result = await run_in_threadpool(_proxy_request, request, body, token, max_tokens)
    if body.get("stream"):
        # Protocol-compatible SSE after a budgeted, non-streaming upstream call.
        if request.url.path.endswith("/messages"):
            text = result["content"][0]["text"]
            events = [
                ("message_start", {"type": "message_start", "message": {**result, "content": [], "stop_reason": None}}),
                ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
                ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
                ("content_block_stop", {"type": "content_block_stop", "index": 0}),
                ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": result["usage"]["output_tokens"]}}),
                ("message_stop", {"type": "message_stop"}),
            ]
            body_sse = "".join(f"event: {name}\ndata: {json.dumps(data)}\n\n" for name, data in events)
        else:
            content = result["choices"][0]["message"]["content"]
            chunk = {"id": result["id"], "object": "chat.completion.chunk", "model": result["model"],
                     "choices": [{"index": 0, "delta": {"role": "assistant", "content": content}, "finish_reason": None}]}
            end = {**chunk, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
            body_sse = f"data: {json.dumps(chunk)}\n\ndata: {json.dumps(end)}\n\ndata: [DONE]\n\n"
        return StreamingResponse(iter([body_sse]), media_type="text/event-stream", headers={"Cache-Control": "no-store"})
    return result


def _proxy_request(request, body, token, max_tokens):
    factory = request.app.state.session_factory
    settings = request.app.state.settings
    with factory() as s:
        valid = s.scalar(select(TrialSessionRow).where(TrialSessionRow.key_hash == key_hash(token)))
        if not valid or valid.status not in ("installing", "ready") or valid.expires_at <= utcnow():
            raise HTTPException(401, "Session key is revoked or expired")
    gateway = make_gateway(factory, settings, transport=getattr(request.app.state, "llm_transport", None))
    if not gateway or not gateway.client:
        raise HTTPException(503, "Configure a model first")
    model = gateway.resolve_model("fast", "trial")
    price, price_version = gateway._price_for(model)
    if price is None:
        raise HTTPException(409, "Set model pricing before using a budgeted trial")
    # Reserve a conservative upper bound atomically. Parallel requests cannot overspend.
    reserve = compute_cost(price, len(json.dumps(body).encode("utf-8")), 0, max_tokens)
    with factory() as s:
        row = s.scalar(select(TrialSessionRow).where(TrialSessionRow.key_hash == key_hash(token)))
        if not row or row.status not in ("installing", "ready") or row.expires_at <= utcnow():
            raise HTTPException(401, "Session key is revoked or expired")
        if row.spent_usd + reserve > row.budget_usd:
            raise HTTPException(429, "Trial session budget exhausted")
        from viberadar.store import repo_ops
        budget = settings.pricing.budget
        if (repo_ops.daily_cost(s) + reserve > Decimal(str(budget.get("daily_hard_cap_usd", 2))) or
            repo_ops.monthly_cost(s) + reserve > Decimal(str(budget.get("monthly_hard_cap_usd", 30)))):
            raise HTTPException(429, "Global model budget exhausted")
        row.spent_usd += reserve
        session_id = row.id
        reservation = repo_ops.record_cost(s, model, 0, 0, 0, price_version, reserve,
            run_id=session_id, stage="trial_reservation", estimated=True)
        reservation_id = reservation.id
        s.commit()
    try:
        # The atomic ledger reservation already checked the global budget, including concurrent calls.
        # Translate protocol while retaining the complete text conversation and roles.
        incoming_anthropic = request.url.path.endswith("/messages")
        messages = body["messages"]
        system = body.get("system", "") if incoming_anthropic else ""
        if not isinstance(system, str):
            raise ValueError("Only text system prompts are supported")
        normalized = []
        for m in messages:
            if not isinstance(m, dict) or m.get("role") not in ("system", "user", "assistant"):
                raise ValueError("Only text conversation roles are supported")
            content = m.get("content", "")
            if isinstance(content, list):
                if any(not isinstance(b, dict) or b.get("type") != "text" or not isinstance(b.get("text", ""), str) for b in content):
                    raise ValueError("Only text content is supported")
                content = "".join(b.get("text", "") for b in content)
            if not isinstance(content, str):
                raise ValueError("Invalid message content")
            if m["role"] == "system":
                system += "\n" + content
            else:
                normalized.append({"role": m["role"], "content": content})
        client = gateway.client
        if gateway.profile.protocol == "anthropic":
            payload = {"model": model, "messages": normalized, "max_tokens": max_tokens, "system": system}
            result = client._post(client._url("/v1/messages", "/messages"), payload,
                {"x-api-key": gateway.profile.api_key, "anthropic-version": "2023-06-01"})
            data = result.data or {}
            text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
            usage = data.get("usage", {})
            inp, out = int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))
            has_usage = "input_tokens" in usage and "output_tokens" in usage
        else:
            payload = {"model": model, "messages": ([{"role": "system", "content": system}] if system else []) + normalized, "max_tokens": max_tokens}
            result = client._post(client._url("/v1/chat/completions", "/chat/completions"), payload,
                                  {"Authorization": "Bearer " + gateway.profile.api_key})
            data = result.data or {}
            text = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
            usage = data.get("usage", {})
            inp, out = int(usage.get("prompt_tokens", 0)), int(usage.get("completion_tokens", 0))
            has_usage = "prompt_tokens" in usage and "completion_tokens" in usage
        if not result.ok or not isinstance(text, str) or inp < 0 or out < 0:
            raise RuntimeError("Model provider request failed")  # Provider error bodies may contain secrets.
        cost = compute_cost(price, inp, 0, out) if has_usage else reserve
        with factory() as s:
            row = s.get(TrialSessionRow, session_id)
            row.spent_usd = max(Decimal(0), row.spent_usd - reserve + cost)
            ledger = s.get(CostLedgerRow, reservation_id)
            ledger.stage, ledger.cost_usd = "trial", cost
            ledger.input_tokens, ledger.output_tokens = (inp, out) if has_usage else (len(json.dumps(body)), max_tokens)
            ledger.estimated = not has_usage
            s.commit()
        if incoming_anthropic:
            return {"id": session_id, "type": "message", "role": "assistant", "model": model,
                    "content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "stop_sequence": None,
                    "usage": {"input_tokens": inp, "output_tokens": out}}
        return {"id": session_id, "object": "chat.completion", "model": model,
                "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": inp, "completion_tokens": out, "total_tokens": inp+out}}
    except ValueError as exc:
        with factory() as s:
            row = s.get(TrialSessionRow, session_id)
            row.spent_usd = max(Decimal(0), row.spent_usd - reserve)
            ledger = s.get(CostLedgerRow, reservation_id)
            ledger.cost_usd, ledger.stage, ledger.estimated = Decimal(0), "trial_rejected", False
            s.commit()
        raise HTTPException(400, str(exc)) from exc
    except Exception:
        # Retain the reservation on ambiguous upstream failure, avoiding unaccounted retries.
        raise HTTPException(502, "Model call failed; retry after checking the local model configuration")
