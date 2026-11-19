import json
import time

import httpx
import pytest
from formwork_core.security import sign_webhook
from formwork_sdk import Formwork, FormworkAPIError, verify_webhook


def test_sdk_calls_the_documented_endpoints_and_raises_typed_errors():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(
            (
                request.method,
                request.url.path,
                request.headers.get("authorization"),
                request.headers.get("idempotency-key"),
            )
        )
        if request.url.path == "/v1/jobs/job_missing":
            return httpx.Response(
                404, json={"error": {"code": "not_found", "message": "job job_missing not found"}}
            )
        if request.url.path.endswith("/result"):
            return httpx.Response(200, content=json.dumps([{"a": 1}]))
        return httpx.Response(200, json={"id": "job_1", "status": "completed"})

    fw = Formwork(
        "http://api",
        "fw_live_x",
        client=httpx.Client(transport=httpx.MockTransport(handler), base_url="http://api"),
    )
    assert fw.create_job("sch_1", ["fil_1"], idempotency_key="k", budget_usd=1.0)["id"] == "job_1"
    assert fw.wait("job_1")["status"] == "completed" and fw.result("job_1") == [{"a": 1}]
    fw.correct("job_1", "ext_1", 5)
    assert (
        seen[0] == ("POST", "/v1/jobs", "Bearer fw_live_x", "k")
        and seen[-1][1] == "/v1/jobs/job_1/review/ext_1"
    )
    with pytest.raises(FormworkAPIError) as err:
        fw.get_job("job_missing")
    assert err.value.status == 404 and err.value.code == "not_found"


def test_sdk_webhook_verification_matches_the_server_signature():
    body = b'{"event":"job.status_changed"}'
    header = sign_webhook("whsec_s", body, int(time.time()))
    assert verify_webhook("whsec_s", body, header) and not verify_webhook("whsec_other", body, header)
    assert not verify_webhook("whsec_s", body, sign_webhook("whsec_s", body, 1000))  # stale timestamp
