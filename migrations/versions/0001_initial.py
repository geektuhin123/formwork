"""initial schema with tenant row-level security

Revision ID: 0001
Revises:
"""

from __future__ import annotations

import os

from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

APP_ROLE = os.environ.get("FORMWORK_DB_APP_ROLE", "formwork_app")

TENANT_TABLES = [
    "api_keys",
    "schemas",
    "files",
    "documents",
    "blocks",
    "jobs",
    "job_documents",
    "job_tasks",
    "extractions",
    "review_gaps",
    "mapping_plans",
    "llm_calls",
    "eval_cases",
    "webhook_deliveries",
]

DDL = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE tenants (
  id text PRIMARY KEY, name text NOT NULL, plan text NOT NULL DEFAULT 'trial',
  webhook_secret text, settings jsonb NOT NULL DEFAULT '{}', created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE api_keys (
  id text PRIMARY KEY, tenant_id text NOT NULL REFERENCES tenants ON DELETE CASCADE, name text,
  hash text NOT NULL UNIQUE, created_at timestamptz NOT NULL DEFAULT now(), revoked_at timestamptz, last_used_at timestamptz
);
CREATE TABLE schemas (
  id text, version int, tenant_id text NOT NULL REFERENCES tenants ON DELETE CASCADE, name text,
  spec jsonb NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (id, version)
);
CREATE TABLE files (
  id text PRIMARY KEY, tenant_id text NOT NULL REFERENCES tenants ON DELETE CASCADE, filename text, mime_type text,
  sha256 text NOT NULL, size_bytes bigint, storage_key text, created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (tenant_id, sha256)
);
CREATE TABLE documents (
  id text PRIMARY KEY, tenant_id text NOT NULL REFERENCES tenants ON DELETE CASCADE, file_id text REFERENCES files ON DELETE CASCADE,
  parent_document_id text, filename text, mime_type text, sha256 text,
  page_count int, doc_class text, parser_version text, meta jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX documents_file_idx ON documents (tenant_id, file_id);
CREATE TABLE blocks (
  id text PRIMARY KEY, tenant_id text NOT NULL REFERENCES tenants ON DELETE CASCADE,
  document_id text NOT NULL REFERENCES documents ON DELETE CASCADE,
  type text, text text, cells jsonb, bbox jsonb, locator text,
  reading_order int, ocr_confidence real,
  embedding vector(1024),
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('simple', left(text, 200000))) STORED,
  CHECK (bbox IS NOT NULL OR locator IS NOT NULL)
);
CREATE INDEX blocks_document_idx ON blocks (document_id, reading_order);
CREATE INDEX blocks_embedding_idx ON blocks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX blocks_tsv_idx ON blocks USING gin (tsv);
CREATE TABLE jobs (
  id text PRIMARY KEY, tenant_id text NOT NULL REFERENCES tenants ON DELETE CASCADE, schema_id text, schema_version int,
  status text NOT NULL, options jsonb NOT NULL DEFAULT '{}', file_ids jsonb NOT NULL DEFAULT '[]',
  cost_usd numeric(10,4) NOT NULL DEFAULT 0,
  stats jsonb NOT NULL DEFAULT '{}', error text, created_at timestamptz NOT NULL DEFAULT now(), finished_at timestamptz
);
CREATE INDEX jobs_tenant_idx ON jobs (tenant_id, created_at DESC);
CREATE TABLE job_documents (
  job_id text REFERENCES jobs ON DELETE CASCADE, document_id text REFERENCES documents ON DELETE CASCADE,
  tenant_id text NOT NULL, PRIMARY KEY (job_id, document_id)
);
CREATE TABLE job_tasks (
  job_id text REFERENCES jobs ON DELETE CASCADE, stage text, document_id text DEFAULT '', field_group text DEFAULT '',
  tenant_id text NOT NULL, status text NOT NULL, result jsonb, error text, duration_ms int, completed_at timestamptz,
  PRIMARY KEY (job_id, stage, document_id, field_group)
);
CREATE TABLE extractions (
  id text PRIMARY KEY, tenant_id text NOT NULL REFERENCES tenants ON DELETE CASCADE, job_id text REFERENCES jobs ON DELETE CASCADE,
  record_key text, field_path text,
  value jsonb, normalized_value text, confidence real,
  evidence jsonb NOT NULL CHECK (jsonb_typeof(evidence) = 'array' AND jsonb_array_length(evidence) > 0),
  alternatives jsonb NOT NULL DEFAULT '[]', extractor text, attempt int, status text, reason text,
  signals jsonb NOT NULL DEFAULT '{}', reviewed_by text, reviewed_at timestamptz
);
CREATE INDEX extractions_job_status_idx ON extractions (job_id, status);
CREATE TABLE review_gaps (
  id text PRIMARY KEY, tenant_id text NOT NULL REFERENCES tenants ON DELETE CASCADE, job_id text REFERENCES jobs ON DELETE CASCADE,
  record_key text, field_path text, reason text, resolved_at timestamptz
);
CREATE TABLE mapping_plans (
  tenant_id text, schema_id text, schema_version int, header_signature text,
  plan jsonb, PRIMARY KEY (tenant_id, schema_id, schema_version, header_signature)
);
CREATE TABLE llm_calls (
  id text PRIMARY KEY, tenant_id text NOT NULL, job_id text, model text, purpose text,
  input_tokens int, output_tokens int, cost_usd numeric(10,6), latency_ms int,
  request_hash text, response jsonb, created_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX llm_calls_job_hash_idx ON llm_calls (tenant_id, job_id, request_hash) WHERE job_id IS NOT NULL;
CREATE TABLE eval_cases (
  id text PRIMARY KEY, tenant_id text NOT NULL, schema_id text, field_path text,
  block_id text, expected jsonb, observed jsonb, source text, context jsonb NOT NULL DEFAULT '{}',
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE webhook_deliveries (
  id text PRIMARY KEY, tenant_id text NOT NULL, job_id text, url text, event text, payload jsonb,
  status text NOT NULL DEFAULT 'pending', attempts int NOT NULL DEFAULT 0, last_error text,
  created_at timestamptz NOT NULL DEFAULT now(), delivered_at timestamptz
);
"""

FUNCTIONS = """
-- Authentication happens before the tenant is known, so the key lookup runs as the table owner.
CREATE FUNCTION fw_lookup_api_key(key_hash text) RETURNS TABLE (key_id text, tenant_id text)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  UPDATE api_keys SET last_used_at = now() WHERE hash = key_hash AND revoked_at IS NULL
  RETURNING api_keys.id, api_keys.tenant_id
$$;
-- Worker recovery: which jobs are still in flight, across tenants (ids only).
CREATE FUNCTION fw_active_jobs() RETURNS TABLE (tenant_id text, job_id text)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT jobs.tenant_id, jobs.id FROM jobs WHERE status IN ('queued','ingesting','extracting','merging','validating')
$$;
CREATE FUNCTION fw_pending_webhooks() RETURNS TABLE (tenant_id text, delivery_id text)
LANGUAGE sql SECURITY DEFINER SET search_path = public AS $$
  SELECT w.tenant_id, w.id FROM webhook_deliveries w WHERE status = 'pending'
$$;
"""


def upgrade() -> None:
    op.execute(DDL)
    op.execute(FUNCTIONS)
    op.execute("ALTER TABLE tenants ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY tenant_isolation ON tenants USING (id = current_setting('app.tenant_id', true)) "
        "WITH CHECK (id = current_setting('app.tenant_id', true))"
    )
    for t in TENANT_TABLES:
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY tenant_isolation ON {t} USING (tenant_id = current_setting('app.tenant_id', true)) "
            f"WITH CHECK (tenant_id = current_setting('app.tenant_id', true))"
        )
    # The application role is a non-owner: RLS applies to it. Ops gives it LOGIN + a password.
    op.execute(
        f"""DO $$ BEGIN
          IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{APP_ROLE}') THEN
            CREATE ROLE {APP_ROLE} NOLOGIN;
          END IF;
        END $$;"""
    )
    op.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {APP_ROLE}")
    op.execute(f"REVOKE INSERT, UPDATE, DELETE ON tenants, api_keys FROM {APP_ROLE}")
    op.execute(
        f"GRANT EXECUTE ON FUNCTION fw_lookup_api_key(text), fw_active_jobs(), fw_pending_webhooks() TO {APP_ROLE}"
    )
    op.execute(
        "REVOKE EXECUTE ON FUNCTION fw_lookup_api_key(text), fw_active_jobs(), fw_pending_webhooks() FROM PUBLIC"
    )


def downgrade() -> None:
    for fn in ("fw_lookup_api_key(text)", "fw_active_jobs()", "fw_pending_webhooks()"):
        op.execute(f"DROP FUNCTION IF EXISTS {fn}")
    for t in [*reversed(TENANT_TABLES), "tenants"]:
        op.execute(f"DROP TABLE IF EXISTS {t} CASCADE")
