"use client";
import { useEffect, useState } from "react";
import { Job, api } from "@/lib/api";
import { Icon } from "./Shell";

type Calls = { calls: number; cost_usd: number; by_purpose: Record<string, { calls: number; cost_usd: number; latency_ms: number }>; stage_ms: Record<string, number> };
const STAGES: [string, string, string][] = [
  ["schema", "Schema", "Target format pinned"], ["ingesting", "Ingest", "Parse, OCR, layout"], ["classify", "Classify", "Document class"],
  ["route", "Route", "Cheapest extractor"], ["extracting", "Extract", "Cited values"], ["merging", "Merge", "One record set"],
  ["validating", "Validate", "Rules and repair"], ["output", "Output", "Files and lineage"],
];
const ORDER = ["queued", "ingesting", "extracting", "merging", "validating", "needs_review", "completed"];
const AT: Record<string, number> = { queued: 1, ingesting: 1, extracting: 4, merging: 5, validating: 6 };

export function RunView({ job, onReview }: { job: Job; onReview: () => void }) {
  const [calls, setCalls] = useState<Calls | null>(null);
  const finished = ["needs_review", "completed", "failed"].includes(job.status);
  useEffect(() => { api<Calls>(`/jobs/${job.id}/calls`).then(setCalls).catch(() => undefined); }, [job.id, job.status, job.progress.tasks_done]);
  const active = finished ? 99 : AT[job.status] ?? 1;
  const pct = Math.round((job.progress.tasks_done / Math.max(1, job.progress.tasks_total)) * 100);
  const purposes = Object.entries(calls?.by_purpose ?? {});
  const total = purposes.reduce((a, [, v]) => a + v.cost_usd, 0) || 1;
  const palette = ["var(--accent)", "var(--good)", "var(--warn)", "var(--crit)", "var(--muted)"];
  return (
    <section className="view" aria-labelledby="h-run">
      <div className="view-head">
        <div><h2 id="h-run">Run</h2><p>Each stage reads from and writes to the database, so a job resumes from its last completed stage. Deterministic parsing runs first; models are called only where they are needed.</p></div>
        {finished && job.status !== "failed" && <button type="button" className="btn primary" onClick={onReview}>Open review</button>}
      </div>
      {job.status === "failed" && <div className="alert" role="alert" style={{ marginBottom: 12 }}>{Icon.bad}<span>{job.error ?? "The job failed."} Any partial result is still available under Export.</span></div>}
      <div className="pipeline" role="list" aria-label="Pipeline stages">
        {STAGES.map(([key, name, sub], i) => {
          const state = finished && job.status !== "failed" ? "done" : i < active ? "done" : i === active ? "active" : "";
          return (
            <div key={key} role="listitem" className={`stage ${state} ${key === "merging" ? "merge" : ""}`}>
              <span className="sn">{String(i + 1).padStart(2, "0")}</span><b>{name}</b>
              <span className="st">{state === "done" ? <>{Icon.ok}Done</> : state === "active" ? "Running…" : sub}</span>
            </div>);
        })}
      </div>
      <div className="run-grid">
        <div className="card card-pad"><h3 style={{ marginBottom: 12 }}>Live metrics</h3>
          <div className="metrics" aria-live="polite">
            <div className="metric"><small>Progress</small><b>{finished ? 100 : pct}%</b></div>
            <div className="metric"><small>Cost so far</small><b>${job.cost_usd.toFixed(3)}</b></div>
            <div className="metric"><small>Fields</small><b>{job.stats.fields_total ?? "—"}</b></div>
            <div className="metric"><small>Auto-approved</small><b>{job.stats.auto_approved ?? "—"}</b></div>
            <div className="metric"><small>In review</small><b>{job.stats.in_review ?? "—"}</b></div>
            <div className="metric"><small>Budget</small><b>{job.budget_usd != null ? `$${job.budget_usd.toFixed(2)}` : "—"}</b></div>
          </div></div>
        <div className="card card-pad"><h3>Model routing</h3>
          <div className="route-bar" role="img" aria-label="Share of model cost by purpose">
            {purposes.map(([p, v], i) => <span key={p} style={{ width: `${(v.cost_usd / total) * 100}%`, background: palette[i % palette.length] }} />)}</div>
          <ul className="log" style={{ maxHeight: 220 }}>
            {purposes.map(([p, v], i) => (
              <li key={p}><span className="t"><i style={{ display: "inline-block", width: 9, height: 9, borderRadius: 2, background: palette[i % palette.length] }} /></span>
                <span className="sg">{v.calls} call{v.calls === 1 ? "" : "s"}</span><span className="m">{p} · ${v.cost_usd.toFixed(4)}</span></li>))}
            {!purposes.length && <li><span className="t" /><span className="sg" /><span className="m muted">No model calls yet.</span></li>}
          </ul></div>
        <div className="card card-pad"><h3 style={{ marginBottom: 8 }}>Time per stage</h3>
          <ul className="log">
            {Object.entries(calls?.stage_ms ?? {}).map(([s, ms]) => <li key={s}><span className="t">{(ms / 1000).toFixed(1)}s</span><span className="sg">{s}</span><span className="m">completed</span></li>)}
            <li><span className="t" /><span className="sg">status</span><span className="m">{job.status.replace("_", " ")} · {job.progress.tasks_done}/{job.progress.tasks_total} tasks · {ORDER.indexOf(job.status) >= 5 ? "ready" : "working"}</span></li>
          </ul></div>
      </div>
    </section>
  );
}
