"""Thin Python client for the Formwork API.

from formwork_sdk import Formwork
fw = Formwork("https://api.example.com", api_key="fw_live_...")
schema = fw.create_schema(fw.compile_schema(json_schema=my_schema)["schema"])
files = [fw.upload_file(p)["file_id"] for p in paths]
job = fw.wait(fw.create_job(schema["schema"]["id"], files)["id"])
rows = fw.result(job["id"], format="json")
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

import httpx

__all__ = ["Formwork", "FormworkAPIError", "verify_webhook"]
TERMINAL = {"completed", "failed", "needs_review"}


class FormworkAPIError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status, self.code, self.message = status, code, message


def verify_webhook(secret: str, body: bytes, signature_header: str, tolerance_s: int = 300) -> bool:
    """Check the X-Formwork-Signature header ('t=<unix>,v1=<hmac-sha256 of "<t>.<body>">')."""
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        ts, given = int(parts["t"]), parts["v1"]
    except (KeyError, ValueError):
        return False
    if abs(time.time() - ts) > tolerance_s:
        return False
    expected = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, given)


class Formwork:
    def __init__(
        self, base_url: str, api_key: str, timeout: float = 60.0, client: httpx.Client | None = None
    ) -> None:
        self._http = client or httpx.Client(base_url=base_url.rstrip("/"), timeout=timeout)
        self._headers = {"Authorization": f"Bearer {api_key}"}

    def _request(self, method: str, path: str, **kw: Any) -> httpx.Response:
        resp = self._http.request(method, path, headers={**self._headers, **kw.pop("headers", {})}, **kw)
        if resp.status_code >= 400:
            try:
                err = resp.json().get("error", {})
            except ValueError:
                err = {}
            raise FormworkAPIError(
                resp.status_code, err.get("code", "error"), err.get("message", resp.text[:200])
            )
        return resp

    def _json(self, method: str, path: str, **kw: Any) -> Any:
        return self._request(method, path, **kw).json()

    # schemas
    def compile_schema(
        self, json_schema: dict[str, Any] | None = None, prompt: str | None = None, name: str | None = None
    ) -> Any:
        body = {
            "source": "json_schema" if json_schema else "prompt",
            "json_schema": json_schema,
            "prompt": prompt,
            "name": name,
        }
        return self._json("POST", "/v1/schemas/compile", json=body)

    def compile_schema_from_sample(self, path: str | Path) -> Any:
        p = Path(path)
        return self._json("POST", "/v1/schemas/compile/sample", files={"file": (p.name, p.read_bytes())})

    @staticmethod
    def _schema_body(schema: dict[str, Any]) -> dict[str, Any]:
        return {
            k: schema[k]
            for k in ("name", "record_grain", "entity_key", "fields", "output_format", "source_hint")
            if k in schema
        }

    def create_schema(self, schema: dict[str, Any]) -> Any:
        return self._json("POST", "/v1/schemas", json=self._schema_body(schema))

    def update_schema(self, schema_id: str, schema: dict[str, Any]) -> Any:
        return self._json("PUT", f"/v1/schemas/{schema_id}", json=self._schema_body(schema))

    def get_schema(self, schema_id: str, version: int | None = None) -> Any:
        return self._json("GET", f"/v1/schemas/{schema_id}", params={"version": version} if version else None)

    # files and jobs
    def upload_file(self, path: str | Path, data: bytes | None = None) -> Any:
        p = Path(path)
        return self._json(
            "POST", "/v1/files", files={"file": (p.name, data if data is not None else p.read_bytes())}
        )

    def parse(self, path: str | Path) -> Any:
        p = Path(path)
        return self._json("POST", "/v1/parse", files={"file": (p.name, p.read_bytes())})

    def create_job(
        self,
        schema_id: str,
        file_ids: list[str],
        schema_version: int | None = None,
        idempotency_key: str | None = None,
        **options: Any,
    ) -> Any:
        body = {
            "schema_id": schema_id,
            "schema_version": schema_version,
            "file_ids": file_ids,
            "options": options,
        }
        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else {}
        return self._json("POST", "/v1/jobs", json=body, headers=headers)

    def get_job(self, job_id: str) -> Any:
        return self._json("GET", f"/v1/jobs/{job_id}")

    def wait(self, job_id: str, timeout: float = 600.0, poll: float = 1.0) -> Any:
        deadline = time.monotonic() + timeout
        while True:
            job = self.get_job(job_id)
            if job["status"] in TERMINAL:
                return job
            if time.monotonic() > deadline:
                raise TimeoutError(f"job {job_id} is still {job['status']}")
            time.sleep(poll)

    def result(self, job_id: str, format: str = "json", include_unreviewed: bool = False) -> Any:
        resp = self._request(
            "GET",
            f"/v1/jobs/{job_id}/result",
            params={"format": format, "include_unreviewed": include_unreviewed},
        )
        return json.loads(resp.content) if format == "json" else resp.content

    def lineage(self, job_id: str) -> Any:
        return self._json("GET", f"/v1/jobs/{job_id}/lineage")

    # review
    def review_queue(self, job_id: str, all: bool = False) -> Any:
        return self._json("GET", f"/v1/jobs/{job_id}/review", params={"all": all})

    def approve(self, job_id: str, item_id: str, reviewer: str | None = None) -> Any:
        return self._json(
            "POST", f"/v1/jobs/{job_id}/review/{item_id}", json={"action": "approve", "reviewer": reviewer}
        )

    def correct(self, job_id: str, item_id: str, value: Any, reviewer: str | None = None) -> Any:
        return self._json(
            "POST",
            f"/v1/jobs/{job_id}/review/{item_id}",
            json={"action": "correct", "value": value, "reviewer": reviewer},
        )

    def pick_alternative(self, job_id: str, item_id: str, index: int, reviewer: str | None = None) -> Any:
        return self._json(
            "POST",
            f"/v1/jobs/{job_id}/review/{item_id}",
            json={"action": "pick_alternative", "alternative_index": index, "reviewer": reviewer},
        )

    def close(self) -> None:
        self._http.close()
