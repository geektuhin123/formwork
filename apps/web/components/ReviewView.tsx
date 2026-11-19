"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Doc, Evidence, Item, Job, TONE_LABEL, TargetSchema, Tone, fmt, leaves, post, tone } from "@/lib/api";
import { ErrorNote, Icon } from "./Shell";
import { SourceViewer } from "./SourceViewer";

type Props = { job: Job; schema: TargetSchema; items: Item[]; docs: Doc[]; reload: () => Promise<void>; onExport: () => void };
const PILL: Record<Tone, string> = { auto: "good", review: "warn", conflict: "crit", approved: "accent", missing: "warn" };
const ICON: Record<Tone, React.ReactNode> = { auto: Icon.ok, review: Icon.warn, conflict: Icon.bad, approved: Icon.ok, missing: Icon.warn };
const open = (t: Tone) => t === "review" || t === "conflict" || t === "missing";
const specPath = (p: string) => p.replace(/\[\d+\]/g, "[]");

export function ReviewView({ job, schema, items, docs, reload, onExport }: Props) {
  const [threshold, setThreshold] = useState<number>(job.options.auto_approve_threshold ?? 0.9);
  const [filter, setFilter] = useState<"all" | "review" | "conflict">("all");
  const [selId, setSelId] = useState<string | null>(null);
  const [src, setSrc] = useState(0);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const specs = useMemo(() => leaves(schema.fields), [schema]);
  const scalars = specs.filter((f) => !f.path.includes("[]"));
  const arrays = schema.fields.filter((f) => f.type === "array");
  const records = useMemo(() => {
    const m = new Map<string, Map<string, Item>>();
    for (const it of items) { if (!m.has(it.record_key)) m.set(it.record_key, new Map()); m.get(it.record_key)!.set(it.field, it); }
    return [...m.entries()].sort(([a], [b]) => a.localeCompare(b));
  }, [items]);
  const docById = useMemo(() => new Map(docs.map((d) => [d.id, d])), [docs]);
  const tones = useMemo(() => new Map(items.map((i) => [i.id, tone(i, threshold)])), [items, threshold]);
  const issues = useMemo(() => items.filter((i) => open(tones.get(i.id)!)), [items, tones]);
  const sel = items.find((i) => i.id === selId) ?? issues[0] ?? items[0];
  const count = (t: Tone) => items.filter((i) => tones.get(i.id) === t).length;

  useEffect(() => { setSrc(0); setEditing(false); setError(null); }, [sel?.id]);

  const act = useCallback(async (item: Item, body: Record<string, unknown>, advance = true) => {
    setBusy(true); setError(null);
    try {
      await post(`/jobs/${job.id}/review/${item.id}`, { ...body, reviewer: "console" });
      const idx = issues.findIndex((i) => i.id === item.id);
      const next = issues.filter((i) => i.id !== item.id)[Math.max(0, idx)] ?? issues.find((i) => i.id !== item.id);
      await reload();
      if (advance && next) setSelId(next.id);
    } catch (e) { setError(e instanceof Error ? e.message : "The action failed"); } finally { setBusy(false); }
  }, [issues, job.id, reload]);

  const nextIssue = useCallback(() => {
    if (!issues.length) return;
    const i = issues.findIndex((x) => x.id === sel?.id);
    setSelId(issues[(i + 1) % issues.length].id);
  }, [issues, sel?.id]);
  const approvable = (i: Item) => i.kind === "field" && i.status !== "failed_validation" && i.value !== null;
  const bulk = items.filter((i) => i.status === "needs_review" && i.confidence >= threshold && approvable(i));
  const approveBulk = async () => { setBusy(true); try { for (const i of bulk) await post(`/jobs/${job.id}/review/${i.id}`, { action: "approve", reviewer: "console" }); await reload(); } finally { setBusy(false); } };

  useEffect(() => {  // keyboard: A approve, E edit, N next issue
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (!sel || busy || e.metaKey || e.ctrlKey || e.altKey || ["INPUT", "TEXTAREA", "SELECT"].includes(t.tagName)) return;
      if (e.key === "n") nextIssue();
      else if (e.key === "a" && approvable(sel) && open(tones.get(sel.id)!)) act(sel, { action: "approve" });
      else if (e.key === "e") { setDraft(sel.value == null ? "" : String(sel.value)); setEditing(true); e.preventDefault(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [sel, busy, nextIssue, act, tones]);

  if (!items.length) return <div className="card empty">No fields were extracted for this job.</div>;
  const t = tones.get(sel.id)!;
  const spec = specs.find((f) => f.path === specPath(sel.field));
  const sources: { ev: Evidence; role: string; alt: boolean }[] = [
    ...sel.evidence.map((ev, i) => ({ ev, role: i === 0 ? "Value" : "Corroborates", alt: false })),
    ...sel.alternatives.flatMap((a) => a.evidence.slice(0, 1).map((ev) => ({ ev, role: "Alternative", alt: true }))),
  ];
  const cur = sources[Math.min(src, sources.length - 1)];
  const meter = t === "conflict" ? "var(--crit)" : t === "auto" ? "var(--good)" : t === "approved" ? "var(--accent)" : "var(--warn)";
  const checks: [string, string][] = [];
  if (sel.reason) checks.push([t === "auto" || t === "approved" ? "ok" : sel.status === "failed_validation" || sel.status === "conflict" ? "bad" : "wn", sel.reason]);
  const sig = sel.signals ?? {};
  if (sig.grounding !== undefined) checks.push(["ok", `Quote found in the source (match ${sig.grounding.toFixed(2)})`]);
  if (sig.rules !== undefined) checks.push([sig.rules ? "ok" : "bad", sig.rules ? "Field rules pass" : "A field rule fails"]);
  if (sig.agreement !== undefined) checks.push([sig.agreement === 1 ? "ok" : "wn", `${Math.round(sig.agreement * 100)}% of sources agree`]);
  if (sig.ocr !== undefined) checks.push([sig.ocr >= 0.85 ? "ok" : "wn", `OCR confidence ${sig.ocr.toFixed(2)}`]);

  const cell = (it: Item | undefined, label: string, key: string) => {
    if (!it) return <span className="cell dim" aria-hidden="true"><span className="val">—</span></span>;
    const ct = tones.get(it.id)!;
    const dim = (filter === "review" && !open(ct)) || (filter === "conflict" && ct !== "conflict");
    return (
      <button key={key} type="button" className={`cell st-${ct} ${dim ? "dim" : ""}`} aria-pressed={sel.id === it.id} data-testid="cell" data-status={ct} data-field={it.field}
        aria-label={`${label}: ${it.kind === "gap" ? "missing" : fmt(it.value)}, ${TONE_LABEL[ct]}`} onClick={() => setSelId(it.id)}>
        <span className="dot" /><span className="val">{it.kind === "gap" ? "missing" : fmt(it.value)}</span>
        <span className="cb"><i style={{ width: `${Math.round(it.confidence * 100)}%` }} /></span>
      </button>);
  };

  return (
    <section className="view" aria-labelledby="h-review">
      <div className="view-head"><div><h2 id="h-review">Review</h2><p>Every value is shown beside the exact region it was read from. Fields under the confidence threshold, and fields where sources disagree, wait for you. Keys: <span className="mono">A</span> approve, <span className="mono">E</span> edit, <span className="mono">N</span> next issue.</p></div></div>
      {issues.length === 0 && <div className="done-banner" role="status" data-testid="done-banner">{Icon.ok}<span>Nothing is waiting for review.</span><button type="button" className="btn" onClick={onExport} style={{ marginLeft: "auto" }}>Go to export</button></div>}
      <div className="summary">
        <div className="sum"><small>Records</small><b>{records.length}</b></div>
        <div className="sum good"><small>Auto-approved</small><b>{count("auto")}</b></div>
        <div className="sum warn"><small>Needs review</small><b data-testid="count-review">{count("review") + count("missing")}</b></div>
        <div className="sum crit"><small>Conflicts</small><b>{count("conflict")}</b></div>
        <div className="sum"><small>Approved by you</small><b>{count("approved")}</b></div>
        <div className="thresh"><label htmlFor="thr"><span>Auto-approve threshold</span><b className="num">{threshold.toFixed(2)}</b></label>
          <input type="range" id="thr" min={0.6} max={0.98} step={0.01} value={threshold} onChange={(e) => setThreshold(+e.target.value)} />
          {bulk.length > 0 && <button type="button" className="btn" disabled={busy} onClick={approveBulk}>Approve {bulk.length} at or above {threshold.toFixed(2)}</button>}</div>
      </div>
      <div className="review">
        <div className="card">
          <div className="grid-tools">
            <div className="seg" role="group" aria-label="Filter cells">
              {([["all", "All fields"], ["review", "Needs review"], ["conflict", "Conflicts"]] as const).map(([f, l]) => <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>{l}</button>)}</div>
            <div className="key-legend">
              <span><i style={{ background: "var(--good)" }} />Auto-approved</span><span><i style={{ background: "var(--warn)" }} />Low confidence</span>
              <span><i style={{ background: "var(--crit)" }} />Sources disagree</span><span><i style={{ background: "var(--accent)" }} />Approved by you</span></div>
          </div>
          <div className="grid-wrap"><table className="rg">
            <thead><tr><th className="rec">Record</th>{scalars.map((f) => <th key={f.path}><span className="fname">{f.path}</span><small>{f.type}</small></th>)}
              {arrays.map((a) => <th key={a.path}><span className="fname">{a.path}</span><small>rows</small></th>)}</tr></thead>
            <tbody>{records.map(([key, fields]) => (
              <tr key={key}><td className="rec"><b>{key}</b><small>{[...new Set([...fields.values()].flatMap((i) => i.evidence.map((e) => e.document_id)))].filter((d) => d !== "manual").length} sources</small></td>
                {scalars.map((f) => <td key={f.path}>{cell(fields.get(f.path), `${f.path} for ${key}`, f.path)}</td>)}
                {arrays.map((a) => {
                  const kids = [...fields.values()].filter((i) => i.field.startsWith(a.path + "["));
                  const worst = kids.find((i) => tones.get(i.id) === "conflict") ?? kids.find((i) => open(tones.get(i.id)!)) ?? kids[0];
                  const n = new Set(kids.map((i) => i.field.slice(0, i.field.indexOf("]") + 1))).size;
                  if (!worst) return <td key={a.path}>{cell(fields.get(a.path), a.path, a.path)}</td>;
                  const wt = tones.get(worst.id)!;
                  return <td key={a.path}><button type="button" className={`cell st-${open(wt) ? wt : "auto"}`} aria-pressed={kids.some((k) => k.id === sel.id)} onClick={() => setSelId(worst.id)}
                    aria-label={`${a.path} for ${key}: ${n} rows`}><span className="dot" /><span className="val">{n} row{n === 1 ? "" : "s"}</span><span className="cb"><i style={{ width: "100%" }} /></span></button></td>;
                })}</tr>))}</tbody>
          </table></div>
        </div>
        <aside className="card insp" aria-label="Field inspector" data-testid="inspector">
          <div className="insp-head"><div style={{ minWidth: 0 }}><div className="fname">{sel.field} · {sel.record_key}</div>
            <div className="insp-val" data-testid="insp-value">{sel.kind === "gap" ? "No value found" : fmt(sel.value)}</div></div>
            <span className={`chip ${PILL[t]}`} data-testid="insp-status">{ICON[t]}{TONE_LABEL[t]}</span></div>
          <div style={{ padding: "10px 16px 12px", borderBottom: "1px solid var(--line)" }}>
            <div className="meter" role="img" aria-label={`Confidence ${sel.confidence.toFixed(2)} against threshold ${threshold.toFixed(2)}`}>
              <i style={{ width: `${sel.confidence * 100}%`, background: meter }} /><span className="th" style={{ left: `${threshold * 100}%` }} /></div>
            <div className="meter-lbl"><span>Confidence <b className="num" style={{ color: "var(--ink)" }}>{sel.confidence.toFixed(2)}</b> · threshold {threshold.toFixed(2)}</span><span>{sel.extractor ?? ""}</span></div>
          </div>
          {sel.field.includes("[") && (() => {
            const root = sel.field.slice(0, sel.field.indexOf("["));
            const kids = items.filter((i) => i.record_key === sel.record_key && i.field.startsWith(root + "["));
            const rows = [...new Set(kids.map((i) => i.field.slice(0, i.field.indexOf("]") + 1)))].sort((a, b) => parseInt(a.split("[")[1]) - parseInt(b.split("[")[1]));
            return <div style={{ padding: "8px 16px", borderBottom: "1px solid var(--line)" }}><small className="muted">Rows of {root}</small>
              <div className="rowgrid">{rows.map((r) => <div className="r" key={r}>{kids.filter((k) => k.field.startsWith(r + ".")).map((k) => cell(k, k.field, k.id))}</div>)}</div></div>;
          })()}
          {cur && <>
            <div className="srctabs" role="tablist" aria-label="Sources for this field">
              {sources.map((s, i) => <button key={i} type="button" className="srctab" role="tab" aria-selected={i === src} onClick={() => setSrc(i)} data-testid="source-tab">
                {s.ev.document_id === "manual" ? "Reviewer" : docById.get(s.ev.document_id)?.filename ?? s.ev.document_id}<em>{s.role}</em></button>)}</div>
            <SourceViewer doc={docById.get(cur.ev.document_id)} evidence={cur.ev} alt={cur.alt} />
          </>}
          <div className="ev-body">
            {cur && <div className="quote"><small>Evidence on {docById.get(cur.ev.document_id)?.filename ?? "reviewer entry"} · {cur.ev.bbox ? `page ${cur.ev.bbox.page}, region cited` : `locator ${cur.ev.locator}`}</small>{cur.ev.quote}</div>}
            {checks.length > 0 && <div><h3 style={{ fontSize: 14, marginBottom: 6 }}>Checks</h3><ul className="checks">{checks.map(([k, m], i) =>
              <li key={i} className={k}>{k === "ok" ? Icon.ok : k === "bad" ? Icon.bad : Icon.warn}<span style={{ color: "var(--ink)" }}>{m}</span></li>)}</ul></div>}
            {sel.alternatives.length > 0 && <div><h3 style={{ fontSize: 14, marginBottom: 6 }}>{sel.status === "conflict" ? "Competing values" : "Other values seen"}</h3>
              <div className="alts">{sel.alternatives.map((a, i) => (
                <div className="alt" key={i}><div><b>{fmt(a.value)}</b><small>{docById.get(a.document_id)?.filename ?? a.document_id} · confidence {a.confidence.toFixed(2)}</small></div>
                  <button type="button" className="btn" disabled={busy} data-testid="use-alt" onClick={() => act(sel, { action: "pick_alternative", alternative_index: i })}>Use this value</button></div>))}</div></div>}
            {spec && <div className="muted" style={{ fontSize: 12.5 }}>Field rule: <span className="mono">{spec.rules[0] ?? "none"}</span> · merge policy <span className="mono">{spec.merge_policy}</span></div>}
            <ErrorNote error={error} />
          </div>
          <div className="actions">
            {editing ? (
              <form className="edit-row" style={{ flex: 1 }} onSubmit={(e) => { e.preventDefault(); act(sel, { action: "correct", value: draft.trim() }).then(() => setEditing(false)); }}>
                <label className="sr" htmlFor="editIn">New value for {sel.field}</label>
                <input id="editIn" autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} onKeyDown={(e) => e.key === "Escape" && setEditing(false)} data-testid="edit-input" />
                <button type="submit" className="btn primary" disabled={busy || !draft.trim()} data-testid="save-edit">Save</button>
                <button type="button" className="btn" onClick={() => setEditing(false)}>Cancel</button></form>
            ) : (<>
              {approvable(sel) && open(t) && <button type="button" className="btn primary" disabled={busy} data-testid="approve" onClick={() => act(sel, { action: "approve" })}>{Icon.ok} Approve</button>}
              <button type="button" className="btn" data-testid="edit" onClick={() => { setDraft(sel.value == null ? "" : String(sel.value)); setEditing(true); }}>{sel.kind === "gap" ? "Enter value" : "Edit value"}</button>
              <span className="spacer" style={{ flex: 1 }} />
              <button type="button" className="btn ghost" onClick={nextIssue} disabled={!issues.length} data-testid="next-issue">Next issue →</button></>)}
          </div>
        </aside>
      </div>
    </section>
  );
}
