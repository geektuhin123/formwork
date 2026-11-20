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

export type LineageNodeKind = "source" | "record" | "field";
export type LineageEdgeRole = "field" | "winner" | "corroborates" | "loser";
export type LineageNode = {
  id: string; kind: LineageNodeKind; label: string; sub?: string; tone?: Tone; itemId?: string; rows?: number;
};
export type LineageEdge = { id: string; from: string; to: string; role: LineageEdgeRole; tone?: Tone };
export type LineageGraph = { nodes: LineageNode[]; edges: LineageEdge[]; truncatedSources: number };
export const MAX_LINEAGE_SOURCES = 14;

/** Node/edge view of one record for the lineage graph: which source documents fed which fields, and who won. */
export function buildLineageGraph(
  items: Item[], docs: Doc[], schema: TargetSchema, recordKey: string, threshold: number,
): LineageGraph {
  const recordItems = items.filter((i) => i.record_key === recordKey);
  const byField = new Map(recordItems.map((i) => [i.field, i]));
  const scalars = leaves(schema.fields).filter((f) => !f.path.includes("[]"));
  const arrays = schema.fields.filter((f) => f.type === "array");
  const docById = new Map(docs.map((d) => [d.id, d]));

  const fieldNodes: LineageNode[] = [];
  const sourceEdges: { docId: string; to: string; role: LineageEdgeRole; tone?: Tone }[] = [];
  const seen = new Set<string>();
  const cites = new Map<string, number>();

  const addField = (path: string, label: string, item: Item | undefined, rows?: number) => {
    const t = item ? tone(item, threshold) : "missing";
    const id = `fld:${path}`;
    fieldNodes.push({ id, kind: "field", label, tone: t, itemId: item?.id, rows });
    if (!item) return;
    const cite = (docId: string, role: LineageEdgeRole) => {
      if (docId === "manual") return;
      const key = `${docId}|${id}|${role}`;
      if (seen.has(key)) return;
      seen.add(key);
      sourceEdges.push({ docId, to: id, role, tone: t });
      cites.set(docId, (cites.get(docId) ?? 0) + 1);
    };
    item.evidence.forEach((ev, i) => cite(ev.document_id, i === 0 ? "winner" : "corroborates"));
    item.alternatives.forEach((a) => cite(a.document_id, "loser"));
  };

  for (const f of scalars) addField(f.path, f.path, byField.get(f.path));
  for (const a of arrays) {
    const kids = recordItems.filter((i) => i.field.startsWith(a.path + "["));
    const rows = new Set(kids.map((i) => i.field.slice(0, i.field.indexOf("]") + 1))).size;
    const worst = kids.find((i) => tone(i, threshold) === "conflict") ?? kids.find((i) => tone(i, threshold) === "review") ?? kids[0];
    addField(a.path, a.path, worst, rows);
  }

  const ranked = [...cites.keys()].sort((x, y) => cites.get(y)! - cites.get(x)!);
  const kept = new Set(ranked.slice(0, MAX_LINEAGE_SOURCES));
  const truncatedSources = Math.max(0, ranked.length - MAX_LINEAGE_SOURCES);

  const sourceNodes: LineageNode[] = [...kept].map((docId) => {
    const d = docById.get(docId);
    return { id: `src:${docId}`, kind: "source", label: d?.filename ?? docId, sub: d?.doc_class ?? (d?.digital ? "digital" : "scan") };
  });

  const edges: LineageEdge[] = [
    ...fieldNodes.map((n): LineageEdge => ({ id: `record|${n.id}`, from: "record", to: n.id, role: "field", tone: n.tone })),
    ...sourceEdges.filter((e) => kept.has(e.docId)).map((e): LineageEdge => ({ id: `${e.docId}|${e.to}|${e.role}`, from: `src:${e.docId}`, to: e.to, role: e.role, tone: e.tone })),
  ];

  return {
    nodes: [{ id: "record", kind: "record", label: recordKey, sub: "merged record" }, ...sourceNodes, ...fieldNodes],
    edges, truncatedSources,
  };
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
