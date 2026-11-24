"use client";
import { useEffect, useState } from "react";
import { BASE, Job } from "@/lib/api";
import { Icon } from "./Shell";

type Fmt = "json" | "csv" | "xlsx" | "api";

function Twirl({ open }: { open: boolean }) {
  return (
    <svg className="tree-twirl-ic" width="10" height="10" viewBox="0 0 16 16" aria-hidden="true" style={{ transform: open ? "rotate(90deg)" : undefined }}>
      <path d="M6 3l5 5-5 5" stroke="currentColor" strokeWidth="2" fill="none" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function fmtVal(v: unknown): string {
  if (v === null) return "null";
  if (typeof v === "string") return v;
  return JSON.stringify(v);
}

function jsonLabel(v: unknown, idx: number): string {
  if (v && typeof v === "object" && !Array.isArray(v)) {
    const rec = v as Record<string, unknown>;
    const key = Object.keys(rec).find((k) => typeof rec[k] === "string" && rec[k]);
    if (key) return String(rec[key]);
  }
  return `[${idx}]`;
}

function DataTree({ value }: { value: unknown }) {
  if (Array.isArray(value)) return <ul className="dtree">{value.map((v, i) => <DataEntry key={i} label={jsonLabel(v, i)} value={v} />)}</ul>;
  if (value && typeof value === "object") return <ul className="dtree">{Object.entries(value as Record<string, unknown>).map(([k, v]) => <DataEntry key={k} label={k} value={v} />)}</ul>;
  return <span className="mono">{fmtVal(value)}</span>;
}

function DataEntry({ label, value }: { label: string; value: unknown }) {
  const [open, setOpen] = useState(true);
  const isBranch = value !== null && typeof value === "object";
  if (!isBranch) {
    return <li className="dtree-row"><span className="dtree-key mono">{label}</span><span className="dtree-val mono">{fmtVal(value)}</span></li>;
  }
  const count = Array.isArray(value) ? value.length : Object.keys(value as object).length;
  return (
    <li className="dtree-row dtree-branch">
      <button type="button" className="tree-twirl" aria-expanded={open} aria-label={`${open ? "Collapse" : "Expand"} ${label}`} onClick={() => setOpen(!open)}>
        <Twirl open={open} />
      </button>
      <span className="dtree-key mono">{label}</span>
      <span className="muted" style={{ fontSize: 11, marginLeft: 6 }}>{Array.isArray(value) ? `${count} item${count === 1 ? "" : "s"}` : `${count} field${count === 1 ? "" : "s"}`}</span>
      {open && <DataTree value={value} />}
    </li>
  );
}

export function ExportView({ job, remaining, defaultFormat, onReview }: { job: Job; remaining: number; defaultFormat: "json" | "csv" | "xlsx"; onReview: () => void }) {
  const [fmt, setFmt] = useState<Fmt>(defaultFormat);
  const [unreviewed, setUnreviewed] = useState(false);
  const [preview, setPreview] = useState("");
  const [view, setView] = useState<"tree" | "raw">("tree");
  const [copied, setCopied] = useState(false);
  const qs = (f: string) => `?format=${f}${unreviewed ? "&include_unreviewed=true" : ""}`;
  const url = (f: string) => `${BASE}/jobs/${job.id}/result${qs(f)}`;
  const snippet = `curl -H "Authorization: Bearer $FORMWORK_API_KEY" \\\n  "$FORMWORK_API_URL/v1/jobs/${job.id}/result${qs("json")}"\n\n# per-field evidence, confidence and alternatives\ncurl -H "Authorization: Bearer $FORMWORK_API_KEY" \\\n  "$FORMWORK_API_URL/v1/jobs/${job.id}/lineage"`;
  useEffect(() => {
    if (fmt === "api") { setPreview(snippet); return; }
    let live = true;
    fetch(url(fmt === "xlsx" ? "csv" : fmt), { cache: "no-store" }).then((r) => r.text()).then((t) => live && setPreview(t.slice(0, 20000))).catch(() => live && setPreview("Preview unavailable."));
    return () => { live = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fmt, unreviewed, job.id, remaining]);
  const copy = async () => { await navigator.clipboard.writeText(preview); setCopied(true); setTimeout(() => setCopied(false), 1500); };
  let parsed: unknown = null;
  let parsedOk = false;
  if (fmt === "json") { try { parsed = JSON.parse(preview); parsedOk = true; } catch { parsedOk = false; } }
  const showTree = fmt === "json" && parsedOk && view === "tree";
  return (
    <section className="view" aria-labelledby="h-export">
      <div className="view-head"><div><h2 id="h-export">Export</h2><p>The dataset in your target format, plus a lineage file that maps every cell to its source region and confidence.</p></div></div>
      {remaining > 0
        ? <div className="alert warn" role="status" style={{ marginBottom: 12 }}>{Icon.warn}<span>{remaining} field{remaining === 1 ? "" : "s"} still wait for review. They are left empty in the export unless you include unreviewed values.</span>
          <button type="button" className="btn" onClick={onReview} style={{ marginLeft: "auto" }}>Finish review</button></div>
        : <div className="done-banner" role="status">{Icon.ok}Every field is approved. This export is final.</div>}
      <div className="export">
        <div className="card">
          <div className="opt"><small>Format</small>
            <div className="seg" role="group" aria-label="Export format">
              {(["json", "csv", "xlsx", "api"] as Fmt[]).map((f) => <button key={f} type="button" aria-pressed={fmt === f} onClick={() => setFmt(f)}>{f.toUpperCase()}</button>)}</div></div>
          <div className="opt"><small>Contents</small>
            <label className="toggle"><input type="checkbox" checked={unreviewed} onChange={(e) => setUnreviewed(e.target.checked)} />Include values that are not yet approved</label></div>
          <div className="opt"><small>Download</small>
            {fmt !== "api" && <a className="btn primary" href={url(fmt)} download data-testid="download">Download {fmt.toUpperCase()}</a>}
            <a className="btn" href={`${BASE}/jobs/${job.id}/lineage`} download={`${job.id}-lineage.json`}>Download lineage.json</a></div>
        </div>
        <div className="card">
          <div className="prev-head"><h3>{fmt === "api" ? "API" : fmt === "xlsx" ? "output.xlsx (shown as CSV)" : `output.${fmt}`}</h3>
            <div className="rowx">
              {fmt === "json" && parsedOk && (
                <div className="seg" role="group" aria-label="Preview view">
                  {(["tree", "raw"] as const).map((v) => <button key={v} type="button" aria-pressed={view === v} onClick={() => setView(v)}>{v === "tree" ? "Tree" : "Raw"}</button>)}
                </div>
              )}
              <button type="button" className="btn" onClick={copy}>{copied ? "Copied" : "Copy"}</button>
            </div>
          </div>
          <div style={{ padding: 12 }} data-testid="export-preview" tabIndex={0}>
            {showTree ? <DataTree value={parsed} /> : <pre className="code">{preview}</pre>}
          </div>
        </div>
      </div>
    </section>
  );
}
