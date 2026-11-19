"use client";
import { useEffect, useState } from "react";
import { BASE, Job } from "@/lib/api";
import { Icon } from "./Shell";

type Fmt = "json" | "csv" | "xlsx" | "api";

export function ExportView({ job, remaining, defaultFormat, onReview }: { job: Job; remaining: number; defaultFormat: "json" | "csv" | "xlsx"; onReview: () => void }) {
  const [fmt, setFmt] = useState<Fmt>(defaultFormat);
  const [unreviewed, setUnreviewed] = useState(false);
  const [preview, setPreview] = useState("");
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
            <button type="button" className="btn" onClick={copy}>{copied ? "Copied" : "Copy"}</button></div>
          <div style={{ padding: 12 }}><pre className="code" data-testid="export-preview" tabIndex={0}>{preview}</pre></div>
        </div>
      </div>
    </section>
  );
}
