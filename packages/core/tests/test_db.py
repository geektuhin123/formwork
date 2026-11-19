import pytest
from formwork_core.db import TenantRepo, tenant_tx
from formwork_core.db.repo import create_api_key, create_tenant, lookup_api_key
from formwork_core.db.session import role_bypasses_rls
from formwork_core.models import Evidence, Extraction, FieldSpec, FieldType, JobStatus, TargetSchema
from formwork_core.security import hash_api_key, sign_webhook, verify_webhook
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

pytestmark = pytest.mark.db


def schema(tid: str) -> TargetSchema:
    return TargetSchema(
        id="sch_1",
        tenant_id=tid,
        name="Invoices",
        version=0,
        record_grain="one row per invoice",
        entity_key=["invoice_number"],
        fields=[FieldSpec(path="invoice_number", type=FieldType.STRING, description="The invoice number")],
    )


async def test_migration_down_and_up(pg):
    from formwork_testkit.pg import migrate
    from sqlalchemy import create_engine

    eng = create_engine(pg.admin_url)
    migrate(pg.admin_url, "base")
    with eng.connect() as c:
        assert c.execute(text("select to_regclass('public.jobs')")).scalar() is None
    migrate(pg.admin_url)
    with eng.connect() as c:
        assert c.execute(text("select to_regclass('public.jobs')")).scalar() == "jobs"
        assert (
            c.execute(text("select count(*) from pg_policies where policyname = 'tenant_isolation'")).scalar()
            == 15
        )
    eng.dispose()


async def test_rls_isolates_tenants(db, admin_db, tenant):
    other = await create_tenant(admin_db, "Other")
    assert not await role_bypasses_rls(db)
    async with tenant_tx(db, tenant["id"]) as conn:
        saved = await TenantRepo(conn, tenant["id"]).save_schema(schema(tenant["id"]))
        assert saved.version == 1
    async with tenant_tx(db, other["id"]) as conn:
        # Even a raw, unfiltered query sees nothing from another tenant.
        assert (await conn.execute(text("select count(*) from schemas"))).scalar() == 0
        with pytest.raises(DBAPIError):  # and cannot write rows for someone else
            await conn.execute(
                text(
                    "insert into schemas (id, version, tenant_id, name, spec) values ('x', 1, :t, 'n', '{}')"
                ),
                {"t": tenant["id"]},
            )
    async with db.begin() as conn:  # no tenant set at all -> nothing visible
        assert (await conn.execute(text("select count(*) from schemas"))).scalar() == 0
    async with tenant_tx(db, tenant["id"]) as conn:
        repo = TenantRepo(conn, tenant["id"])
        assert (await repo.save_schema(schema(tenant["id"]))).version == 2
        assert (await repo.get_schema("sch_1")).version == 2 and (
            await repo.get_schema("sch_1", 1)
        ).version == 1


async def test_api_key_hash_lookup_and_app_role_cannot_mint_keys(db, admin_db, tenant):
    key = await create_api_key(admin_db, tenant["id"], "pepper")
    assert key.startswith("fw_live_")
    assert await lookup_api_key(db, hash_api_key(key, "pepper")) is not None
    assert await lookup_api_key(db, hash_api_key(key, "wrong-pepper")) is None
    async with admin_db.begin() as conn:
        stored = (await conn.execute(text("select hash from api_keys"))).scalar()
    assert key not in stored and len(stored) == 64
    with pytest.raises(DBAPIError):
        async with tenant_tx(db, tenant["id"]) as conn:
            await conn.execute(
                text("insert into api_keys (id, tenant_id, hash) values ('k', :t, 'h')"), {"t": tenant["id"]}
            )


async def test_extraction_evidence_check_constraint_and_roundtrip(db, tenant):
    ev = Evidence(block_id="b1", document_id="d1", locator="Sheet1!A2:F2", quote="INV-1")
    async with tenant_tx(db, tenant["id"]) as conn:
        repo = TenantRepo(conn, tenant["id"])
        job = await repo.create_job("sch_1", 1, [], {})
        await repo.replace_extractions(
            job["id"],
            [
                Extraction(
                    record_key="inv-1",
                    field_path="invoice_number",
                    value="INV-1",
                    confidence=0.9,
                    evidence=[ev],
                    extractor="tabular",
                )
            ],
        )
        got = await repo.list_extractions(job["id"])
        assert got[0].evidence[0].quote == "INV-1" and got[0].id
        assert await repo.set_job_status(job["id"], JobStatus.INGESTING)
        assert not await repo.set_job_status(job["id"], JobStatus.INGESTING)
        await repo.complete_task(job["id"], "ingest", "d1", result={"ok": 1})
        assert (await repo.task(job["id"], "ingest", "d1"))["result"] == {"ok": 1}
    with pytest.raises(DBAPIError):
        async with tenant_tx(db, tenant["id"]) as conn:
            await conn.execute(
                text("insert into extractions (id, tenant_id, job_id, evidence) values ('e', :t, :j, '[]')"),
                {"t": tenant["id"], "j": job["id"]},
            )


def test_webhook_signature():
    sig = sign_webhook("whsec_x", b'{"a":1}', timestamp=1000)
    assert verify_webhook("whsec_x", b'{"a":1}', sig, now=1100)
    assert not verify_webhook("whsec_x", b'{"a":2}', sig, now=1100)
    assert not verify_webhook("other", b'{"a":1}', sig, now=1100)
    assert not verify_webhook("whsec_x", b'{"a":1}', sig, now=9999)  # replay outside tolerance
    assert not verify_webhook("whsec_x", b'{"a":1}', "garbage")
