"use client";
import { useRouter } from "next/navigation";
import { Suspense, useCallback, useEffect, useRef, useState } from "react";
import { SchemaEditor, weakCount } from "@/components/SchemaEditor";
import { ErrorNote, Icon, Shell, Step } from "@/components/Shell";
import { FileOut, Job, TargetSchema, Warning, api, post, put, schemaBody, upload } from "@/lib/api";

type Mode = "sample" | "json" | "prompt" | "saved";
type Draft = { schema: TargetSchema; warnings: Warning[] };
const EXAMPLE_PROMPT = "From each supplier invoice I need: invoice number, invoice date, vendor name, vendor GSTIN, PO number, subtotal, CGST, SGST and total amount";

function NewJob() {
  const router = useRouter();
  const [step, setStep] = useState<"schema" | "sources">("schema");
  const [mode, setMode] = useState<Mode>("prompt");
  const [schema, setSchema] = useState<TargetSchema | null>(null);
  const [saved, setSaved] = useState<{ id: string; version: number; name: string }[]>([]);
  const [jsonText, setJsonText] = useState("");
  const [prompt, setPrompt] = useState(EXAMPLE_PROMPT);
  const [files, setFiles] = useState<FileOut[]>([]);
  const [threshold, setThreshold] = useState(0.9);
  const [budget, setBudget] = useState(2.5);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [over, setOver] = useState(false);
  const picker = useRef<HTMLInputElement>(null);

  useEffect(() => { api<{ id: string; version: number; name: string }[]>("/schemas").then(setSaved).catch(() => undefined); }, []);

  const run = useCallback(async (label: string, fn: () => Promise<void>) => {
    setBusy(label); setError(null);
    try { await fn(); } catch (e) { setError(e instanceof Error ? e.message : "Something went wrong"); } finally { setBusy(null); }
  }, []);

  const compileSample = (file: File) => run("Reading the sample…", async () => setSchema((await upload<Draft>("/schemas/compile/sample", file)).schema));
  const compileJson = () => run("Compiling…", async () => {
    let parsed: unknown;
    try { parsed = JSON.parse(jsonText); } catch { throw new Error("That is not valid JSON."); }
    setSchema((await post<Draft>("/schemas/compile", { source: "json_schema", json_schema: parsed })).schema);
  });
  const compilePrompt = () => run("Drafting fields…", async () => setSchema((await post<Draft>("/schemas/compile", { source: "prompt", prompt })).schema));
  const loadSaved = (id: string) => run("Loading…", async () => setSchema((await api<Draft>(`/schemas/${id}`)).schema));

  const addFiles = (list: FileList | File[]) => run("Uploading…", async () => {
    for (const f of Array.from(list)) {
      const out = await upload<FileOut>("/files", f);
      setFiles((prev) => (prev.some((p) => p.file_id === out.file_id) ? prev : [...prev, out]));
    }
  });

  const start = () => run("Starting the job…", async () => {
    if (!schema) return;
    const isSaved = schema.version > 0;
    const stored = isSaved ? await put<Draft>(`/schemas/${schema.id}`, schemaBody(schema)) : await post<Draft>("/schemas", schemaBody(schema));
    const job = await post<Job>("/jobs", { schema_id: stored.schema.id, schema_version: stored.schema.version, file_ids: files.map((f) => f.file_id),
      options: { auto_approve_threshold: threshold, budget_usd: budget } });
    router.push(`/jobs/${job.id}`);
  });

  const weak = schema ? weakCount(schema) : 0;
  const steps: Step[] = [
    { key: "schema", label: "Target format", sub: schema ? `${schema.fields.length} fields` : "Define the output", state: schema ? (weak ? "attn" : "done") : "todo", onClick: () => setStep("schema") },
    { key: "sources", label: "Sources", sub: files.length ? `${files.length} file${files.length === 1 ? "" : "s"}` : "Add documents", state: files.length ? "done" : "todo", onClick: () => schema && setStep("sources"), disabled: !schema },
    { key: "run", label: "Run", sub: "Not started", state: "todo", disabled: true },
    { key: "review", label: "Review", sub: "After the run", state: "todo", disabled: true },
    { key: "export", label: "Export", sub: "After review", state: "todo", disabled: true },
  ];

  return (
    <Shell crumb="New job" steps={steps} current={step}>
      <ErrorNote error={error} />
      {busy && <div className="alert info" role="status" style={{ marginBottom: 12 }}>{busy}</div>}
      {step === "schema" && (
        <section className="view" aria-labelledby="h-schema">
          <div className="view-head">
            <div><h2 id="h-schema">Target format</h2><p>Show Formwork the dataset you want. Share a sample file, a JSON Schema, or describe it. Every source will be transformed into exactly this shape.</p></div>
            <button type="button" className="btn primary" disabled={!schema || !schema.entity_key.length} onClick={() => setStep("sources")}>Continue to sources</button>
          </div>
          <div className="card src-panel" style={{ marginBottom: 16 }}>
            <div className="tabbar" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, padding: "12px 16px", borderBottom: "1px solid var(--line)", flexWrap: "wrap" }}>
              <h3>Define it from</h3>
              <div className="seg" role="group" aria-label="How to define the target format">
                {([["prompt", "Describe it"], ["sample", "Sample file"], ["json", "JSON Schema"], ["saved", "Saved schema"]] as [Mode, string][]).map(([m, label]) => (
                  <button key={m} type="button" aria-pressed={mode === m} onClick={() => setMode(m)}>{label}</button>))}
              </div>
            </div>
            <div className="card-pad stack">
              {mode === "sample" && (
                <label className="drop" style={{ cursor: "pointer" }}>
                  <b>Drop a sample of the output you want</b><span className="muted">CSV, XLSX or JSON. Column names and a few rows are enough.</span>
                  <input type="file" className="sr" accept=".csv,.xlsx,.json" data-testid="sample-input" onChange={(e) => e.target.files?.[0] && compileSample(e.target.files[0])} />
                  <span className="btn">Choose file</span>
                </label>)}
              {mode === "json" && (<>
                <textarea className="prompt mono" style={{ minHeight: 180 }} aria-label="JSON Schema" placeholder='{"type": "object", "properties": {"invoice_number": {"type": "string", "description": "…"}}}'
                  value={jsonText} onChange={(e) => setJsonText(e.target.value)} />
                <div><button type="button" className="btn primary" onClick={compileJson} disabled={!jsonText.trim()}>Compile to fields</button></div></>)}
              {mode === "prompt" && (<>
                <textarea className="prompt" aria-label="Describe the dataset" value={prompt} onChange={(e) => setPrompt(e.target.value)} />
                <div><button type="button" className="btn primary" onClick={compilePrompt} disabled={!prompt.trim()}>Compile to fields</button></div></>)}
              {mode === "saved" && (saved.length
                ? <div className="rowx">{saved.map((s) => <button key={s.id} type="button" className="btn" onClick={() => loadSaved(s.id)}>{s.name} <span className="tchip">v{s.version}</span></button>)}</div>
                : <p className="muted">No saved schemas yet.</p>)}
            </div>
          </div>
          {schema && <SchemaEditor schema={schema} onChange={setSchema} />}
        </section>
      )}
      {step === "sources" && schema && (
        <section className="view" aria-labelledby="h-sources">
          <div className="view-head">
            <div><h2 id="h-sources">Sources</h2><p>Add everything that describes the same records: PDFs, scans, photos, spreadsheets, Word files, emails. Up to 50 files per job; they are merged on <span className="mono">{schema.entity_key.join(" + ")}</span>.</p></div>
            <button type="button" className="btn primary" disabled={!files.length || !!busy} onClick={start} data-testid="start-job">Run job</button>
          </div>
          <div className={`drop ${over ? "over" : ""}`} onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
            onDrop={(e) => { e.preventDefault(); setOver(false); if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files); }}>
            <b>Drop files here</b>
            <span className="muted">PDF, PNG/JPG/TIFF, XLSX, CSV, DOCX, EML · 50 MB each</span>
            <input ref={picker} type="file" multiple className="sr" data-testid="source-input" accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,.webp,.xlsx,.csv,.docx,.eml"
              onChange={(e) => e.target.files && addFiles(e.target.files)} />
            <button type="button" className="btn" onClick={() => picker.current?.click()}>Browse files</button>
          </div>
          <div className="src-grid">
            {files.map((f) => (
              <div key={f.file_id} className="card card-pad src" style={{ display: "flex", gap: 12 }}>
                <div className="ico" aria-hidden="true">{f.filename.split(".").pop()?.slice(0, 4).toUpperCase()}</div>
                <div className="meta"><div className="nm">{f.filename}</div><div className="sub">{f.mime_type} · {(f.size_bytes / 1024).toFixed(0)} KB</div>
                  <div className="row">{f.deduplicated && <span className="chip">Already uploaded, reused</span>}
                    <button type="button" className="btn ghost" style={{ minHeight: 28, padding: "2px 8px" }} onClick={() => setFiles(files.filter((x) => x.file_id !== f.file_id))}>Remove</button></div></div>
              </div>))}
          </div>
          <div className="card card-pad" style={{ marginTop: 16 }}>
            <h3 style={{ marginBottom: 12 }}>Run options</h3>
            <div className="facts">
              <div className="thresh" style={{ maxWidth: "none" }}><label htmlFor="thr0"><span>Auto-approve threshold</span><b className="num">{threshold.toFixed(2)}</b></label>
                <input type="range" id="thr0" min={0.6} max={0.98} step={0.01} value={threshold} onChange={(e) => setThreshold(+e.target.value)} />
                <span className="muted" style={{ fontSize: 12.5 }}>Fields below this confidence wait for a reviewer.</span></div>
              <label className="fact"><small>Budget for this job (USD)</small>
                <input className="field-input num" type="number" min={0.1} step={0.1} value={budget} onChange={(e) => setBudget(Math.max(0.1, +e.target.value))} />
                <span className="muted" style={{ fontSize: 12.5 }}>Escalation stops at the budget; remaining fields go to review.</span></label>
            </div>
          </div>
        </section>
      )}
    </Shell>
  );
}

export default function Page() {
  return <Suspense><NewJob /></Suspense>;
}
