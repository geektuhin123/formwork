export type FieldSpec = {
  path: string; type: string; description: string; required: boolean; enum_values?: string[] | null; format?: string | null;
  examples: string[]; rules: string[]; merge_policy: string; children: FieldSpec[];
};
export type TargetSchema = {
  id: string; name: string; version: number; record_grain: string; entity_key: string[]; fields: FieldSpec[];
  output_format: "json" | "csv" | "xlsx"; source_hint?: string | null;
};
export type Warning = { path: string; words: number; message: string };
export type BBox = { page: number; x0: number; y0: number; x1: number; y1: number };
export type Evidence = { block_id: string; document_id: string; bbox?: BBox; locator?: string; quote: string };
export type Alternative = { value: unknown; confidence: number; extractor: string; document_id: string; quote: string; evidence: Evidence[] };
export type Item = {
  id: string; kind: "field" | "gap"; record_key: string; field: string; value: unknown; confidence: number;
  status: "approved" | "needs_review" | "conflict" | "failed_validation" | "extracted" | "missing";
  extractor?: string; reason?: string | null; signals?: Record<string, number>; evidence: Evidence[]; alternatives: Alternative[];
};
export type Job = {
  id: string; status: string; schema_id: string; schema_version: number; file_ids: string[]; document_ids: string[];
  cost_usd: number; budget_usd: number | null; progress: { tasks_done: number; tasks_total: number; last_stage: string | null };
  stats: Record<string, any>; options: Record<string, any>; error: string | null; created_at: string | null; finished_at: string | null;
};
export type Doc = { id: string; file_id: string; filename: string; mime_type: string; page_count: number; doc_class: string | null; digital: boolean; parent_document_id: string | null };
export type FileOut = { file_id: string; filename: string; mime_type: string; size_bytes: number; deduplicated: boolean };

export const BASE = "/api/fw/v1";

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, { ...init, cache: "no-store" });
  if (!res.ok) {
    let code = "error", message = `Request failed (${res.status})`;
    try { const j = await res.json(); code = j.error?.code ?? code; message = j.error?.message ?? message; } catch { /* not JSON */ }
    throw new ApiError(res.status, code, message);
  }
  return res.json() as Promise<T>;
}

export const post = <T,>(path: string, body: unknown, headers: Record<string, string> = {}) =>
  api<T>(path, { method: "POST", headers: { "Content-Type": "application/json", ...headers }, body: JSON.stringify(body) });
export const put = <T,>(path: string, body: unknown) =>
  api<T>(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

export async function upload<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file, file.name);
  return api<T>(path, { method: "POST", body: form });
}

export function leaves(fields: FieldSpec[]): FieldSpec[] {
  return fields.flatMap((f) => (f.children?.length ? leaves(f.children) : [f]));
}

export function schemaBody(s: TargetSchema) {
  return { name: s.name, record_grain: s.record_grain, entity_key: s.entity_key, fields: s.fields, output_format: s.output_format, source_hint: s.source_hint ?? null };
}

export function fmt(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  return String(v);
}

export type Tone = "auto" | "review" | "conflict" | "approved" | "missing";
/** Display state of a cell. The threshold slider re-triages auto-approved values on the client. */
export function tone(item: Item, threshold: number): Tone {
  if (item.kind === "gap") return "missing";
  if (item.status === "conflict") return "conflict";
  if (item.status === "failed_validation" || item.status === "needs_review" || item.status === "extracted") return "review";
  if (item.extractor === "human" || (item.reason ?? "").includes("reviewer")) return "approved";
  return item.confidence < threshold ? "review" : "auto";
}
export const TONE_LABEL: Record<Tone, string> = {
  auto: "Auto-approved", review: "Needs review", conflict: "Sources disagree", approved: "Approved by you", missing: "Missing",
};
export const WEAK_WORDS = 8;
