import json

import httpx
import pytest
from formwork_core.db import TenantRepo, tenant_tx
from formwork_core.db.repo import create_api_key, create_tenant
from formwork_core.security import verify_webhook
from formwork_testkit import synth
from formwork_testkit.schemas import invoice_json_schema, reconciliation_json_schema

pytestmark = pytest.mark.db


async def make_schema(api, js):
    draft = (await api.post("/v1/schemas/compile", json={"source": "json_schema", "json_schema": js})).json()
    body = {
        k: draft["schema"][k]
        for k in ("name", "record_grain", "entity_key", "fields", "output_format", "source_hint")
    }
    r = await api.post("/v1/schemas", json=body)
    assert r.status_code == 201, r.text
    return r.json()["schema"], body


async def upload(api, name, data):
    r = await api.post("/v1/files", files={"file": (name, data)})
    assert r.status_code == 201, r.text
    return r.json()


async def test_auth_is_required_and_keys_are_tenant_scoped(api, admin_db):
    anon = httpx.AsyncClient(transport=api._transport, base_url="http://api")
    assert (await anon.get("/v1/jobs")).status_code == 401
    assert (await anon.get("/v1/jobs", headers={"Authorization": "Bearer fw_live_wrong"})).status_code == 401
    assert (await anon.get("/healthz")).json() == {"status": "ok"}
    schema, _ = await make_schema(api, invoice_json_schema())
    other = await create_tenant(admin_db, "Other")
    other_key = await create_api_key(admin_db, other["id"], "test-pepper")
    r = await anon.get(f"/v1/schemas/{schema['id']}", headers={"Authorization": f"Bearer {other_key}"})
    assert (
        r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    )  # another tenant's schema does not exist for you
    assert (await api.get("/v1/account")).json()["webhook_secret"].startswith("whsec_")


async def test_schema_compile_save_version_and_validation(api):
    weak = {"type": "object", "properties": {"invoice_number": {"type": "string", "description": "Number"}}}
    draft = (
        await api.post("/v1/schemas/compile", json={"source": "json_schema", "json_schema": weak})
    ).json()
    assert draft["warnings"][0]["path"] == "invoice_number" and draft["schema"]["version"] == 0
    schema, body = await make_schema(api, invoice_json_schema())
    assert schema["version"] == 1
    v2 = (await api.put(f"/v1/schemas/{schema['id']}", json={**body, "name": "Renamed"})).json()["schema"]
    assert (
        v2["version"] == 2
        and (await api.get(f"/v1/schemas/{schema['id']}?version=1")).json()["schema"]["name"]
        == schema["name"]
    )
    assert (await api.get("/v1/schemas")).json()[0]["version"] == 2
    bad_rule = json.loads(json.dumps(body))
    bad_rule["fields"][0]["rules"] = ["import os"]
    assert (await api.post("/v1/schemas", json=bad_rule)).status_code == 422
    assert (await api.post("/v1/schemas", json={**body, "entity_key": ["nope"]})).status_code == 422
    sample = await api.post(
        "/v1/schemas/compile/sample", files={"file": ("t.csv", b"po_number,total\nPO-1,10.5\n")}
    )
    assert [f["path"] for f in sample.json()["schema"]["fields"]] == ["po_number", "total"]
    prompt = await api.post(
        "/v1/schemas/compile", json={"source": "prompt", "prompt": "I need: invoice number, total amount"}
    )
    assert prompt.json()["schema"]["source_hint"] == "prompt"


async def test_file_upload_limits_dedupe_and_parse(api):
    pdf = synth.invoice_pdf(synth.make_invoice(0))
    a, b = await upload(api, "a.pdf", pdf), await upload(api, "renamed.pdf", pdf)
    assert a["file_id"] == b["file_id"] and b["deduplicated"] and a["mime_type"] == "application/pdf"
    assert (await api.post("/v1/files", files={"file": ("x.exe", b"MZ\x90\x00binary")})).status_code == 415
    api.fw_state.settings.max_upload_bytes = 100
    assert (await api.post("/v1/files", files={"file": ("big.pdf", pdf)})).status_code == 413
    api.fw_state.settings.max_upload_bytes = 50 * 1024 * 1024
    parsed = (await api.post("/v1/parse", files={"file": ("a.pdf", pdf)})).json()
    assert parsed["document"]["blocks"] and "embedding" not in parsed["document"]["blocks"][0]
    assert (await api.get(f"/v1/files/{a['file_id']}/content")).content == pdf


async def test_three_file_job_end_to_end_with_signed_webhook_review_and_export(api, monkeypatch):
    received = []
    real_post = httpx.AsyncClient.post

    async def capture(self, url, **kw):  # the webhook receiver
        if str(url).startswith("http://hooks.test"):
            received.append((kw["content"], kw["headers"]["X-Formwork-Signature"]))
            return httpx.Response(200)
        return await real_post(self, url, **kw)

    monkeypatch.setattr(httpx.AsyncClient, "post", capture)
    schema, _ = await make_schema(api, reconciliation_json_schema())
    o = synth.make_order(3)  # short shipment: received < ordered
    files = [
        await upload(api, "po.xlsx", synth.po_register_xlsx([o])),
        await upload(api, "inv.pdf", synth.invoice_pdf(o.invoice)),
        await upload(api, "grn.pdf", synth.scan_pdf(synth.grn_pdf(o), seed=3)),
    ]
    r = await api.post(
        "/v1/jobs",
        headers={"Idempotency-Key": "abc"},
        json={
            "schema_id": schema["id"],
            "file_ids": [f["file_id"] for f in files],
            "options": {
                "webhook_url": "http://hooks.test/formwork",
                "budget_usd": 2.5,
                "source_priority": [files[0]["file_id"]],
            },
        },
    )
    assert r.status_code == 202 and r.json()["status"] == "queued"
    job_id = r.json()["id"]
    again = await api.post(
        "/v1/jobs",
        headers={"Idempotency-Key": "abc"},
        json={"schema_id": schema["id"], "file_ids": [files[0]["file_id"]]},
    )
    assert again.json()["id"] == job_id  # same Idempotency-Key -> same job
    await api.fw_state.queue.drain()

    job = (await api.get(f"/v1/jobs/{job_id}")).json()
    assert job["status"] == "needs_review" and len(job["document_ids"]) == 3 and 0 < job["cost_usd"] <= 2.5
    assert job["stats"]["records"] == 1 and job["progress"]["tasks_done"] == job["progress"]["tasks_total"]
    # webhooks: one per status change, each signature verifies with the tenant secret
    secret = api.fw_tenant["webhook_secret"]
    statuses = [json.loads(body)["status"] for body, _ in received]
    assert statuses == ["ingesting", "extracting", "merging", "validating", "needs_review"]
    assert all(verify_webhook(secret, body, sig) for body, sig in received)
    assert not verify_webhook("whsec_wrong", *received[0]) and not verify_webhook(
        secret, received[0][0] + b" ", received[0][1]
    )

    lineage = (await api.get(f"/v1/jobs/{job_id}/lineage")).json()
    po = lineage["records"][o.po_number.lower()]["po_number"]
    assert len(po["evidence"]) == 3 and {"locator" in e or "bbox" in e for e in po["evidence"]} == {True}
    queue = (await api.get(f"/v1/jobs/{job_id}/review")).json()
    short = next(i for i in queue["items"] if i["field"] == "received_qty")
    assert short["status"] == "failed_validation" and queue["remaining"] == len(queue["items"])
    partial = await api.get(f"/v1/jobs/{job_id}/result?format=json")
    assert partial.headers["X-Formwork-Partial"] == "true" and partial.json()[0]["received_qty"] is None

    # review: a correction becomes an eval case; clearing the queue completes the job
    bad = await api.post(
        f"/v1/jobs/{job_id}/review/{short['id']}", json={"action": "correct", "value": "many"}
    )
    assert bad.status_code == 422
    fixed = (
        await api.post(
            f"/v1/jobs/{job_id}/review/{short['id']}",
            json={"action": "correct", "value": 999, "reviewer": "asha"},
        )
    ).json()
    assert fixed["eval_case_id"] and fixed["item"]["value"] == 999 and fixed["item"]["extractor"] == "human"
    assert fixed["item"]["alternatives"][-1]["value"] == o.received_qty
    for item in queue["items"]:
        if item["id"] != short["id"]:
            r = await api.post(f"/v1/jobs/{job_id}/review/{item['id']}", json={"action": "approve"})
            assert r.status_code == 200, r.text
    await api.fw_state.queue.drain()
    assert (await api.get(f"/v1/jobs/{job_id}")).json()["status"] == "completed"
    assert json.loads(received[-1][0])["status"] == "completed"
    done = await api.get(f"/v1/jobs/{job_id}/result?format=csv")
    assert (
        done.headers["X-Formwork-Partial"] == "false"
        and b"999" in done.content
        and o.po_number.encode() in done.content
    )
    assert (await api.get(f"/v1/jobs/{job_id}/result?format=xlsx")).content[:2] == b"PK"
    async with tenant_tx(api.fw_state.engine, api.fw_tenant["id"]) as conn:
        cases = await TenantRepo(conn, api.fw_tenant["id"]).list_eval_cases()
    assert len(cases) == 1 and cases[0]["expected"] == 999 and cases[0]["context"]["block_text"]
    calls = (await api.get(f"/v1/jobs/{job_id}/calls")).json()
    assert calls["calls"] > 0 and "extract:text" in calls["by_purpose"]
    docs = (await api.get(f"/v1/jobs/{job_id}/documents")).json()
    scan = next(d for d in docs if not d["digital"] and d["mime_type"] == "application/pdf")
    assert (await api.get(f"/v1/documents/{scan['id']}/pages/1")).content[:4] == b"\x89PNG"


async def test_job_validation_errors(api):
    schema, _ = await make_schema(api, invoice_json_schema())
    f = await upload(api, "a.pdf", synth.invoice_pdf(synth.make_invoice(1)))
    post = lambda **o: api.post(
        "/v1/jobs", json={"schema_id": schema["id"], "file_ids": [f["file_id"]], "options": o}
    )
    assert (await post(webhook_url="ftp://x")).status_code == 422
    assert (await post(budget_usd=10_000)).status_code == 422
    assert (await post(auto_approve_threshold=2)).status_code == 422
    assert (
        await api.post("/v1/jobs", json={"schema_id": schema["id"], "file_ids": ["fil_nope"]})
    ).status_code == 404
    assert (
        await api.post("/v1/jobs", json={"schema_id": "sch_nope", "file_ids": [f["file_id"]]})
    ).status_code == 404
    api.fw_state.settings.webhook_allow_private_hosts = False
    assert (
        await post(webhook_url="http://169.254.169.254/latest/meta-data")
    ).status_code == 422  # SSRF guard
    assert (await post(webhook_url="http://localhost:8000/x")).status_code == 422
