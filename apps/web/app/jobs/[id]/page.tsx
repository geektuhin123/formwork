"use client";
import { useParams, useRouter, useSearchParams } from "next/navigation";
import { Suspense, useCallback, useEffect, useState } from "react";
import { ExportView } from "@/components/ExportView";
import { ReviewView } from "@/components/ReviewView";
import { RunView } from "@/components/RunView";
import { ErrorNote, Shell, StatusChip, Step } from "@/components/Shell";
import { Doc, Item, Job, TargetSchema, api } from "@/lib/api";

type View = "run" | "review" | "export";
const SETTLED = ["needs_review", "completed", "failed"];

function JobPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const params = useSearchParams();
  const [job, setJob] = useState<Job | null>(null);
  const [schema, setSchema] = useState<TargetSchema | null>(null);
  const [items, setItems] = useState<Item[]>([]);
  const [docs, setDocs] = useState<Doc[]>([]);
  const [error, setError] = useState<string | null>(null);
  const view = (params.get("step") as View | null) ?? (job && SETTLED.includes(job.status) && job.status !== "failed" ? "review" : "run");
  const go = (v: View) => router.replace(`/jobs/${id}?step=${v}`);

  const loadReview = useCallback(async () => {
    const [q, j] = await Promise.all([api<{ items: Item[] }>(`/jobs/${id}/review?all=true`), api<Job>(`/jobs/${id}`)]);
    setItems(q.items); setJob(j);
  }, [id]);

  useEffect(() => {
    let live = true, timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const j = await api<Job>(`/jobs/${id}`);
        if (!live) return;
        setJob(j);
        if (SETTLED.includes(j.status)) {
          const [s, d] = await Promise.all([api<{ schema: TargetSchema }>(`/schemas/${j.schema_id}?version=${j.schema_version}`), api<Doc[]>(`/jobs/${id}/documents`)]);
          if (!live) return;
          setSchema(s.schema); setDocs(d); await loadReview();
        } else timer = setTimeout(tick, 1200);
      } catch (e) { if (live) setError(e instanceof Error ? e.message : "Could not load the job"); }
    };
    tick();
    return () => { live = false; clearTimeout(timer); };
  }, [id, loadReview]);

  const remaining = items.filter((i) => i.status !== "approved").length;
  const settled = !!job && SETTLED.includes(job.status);
  const steps: Step[] = [
    { key: "schema", label: "Target format", sub: schema ? `${schema.name} v${schema.version}` : "Pinned", state: "done", disabled: true },
    { key: "sources", label: "Sources", sub: job ? `${job.file_ids.length} files` : "", state: "done", disabled: true },
    { key: "run", label: "Run", sub: job ? job.status.replace("_", " ") : "", state: settled ? (job!.status === "failed" ? "attn" : "done") : "todo", onClick: () => go("run") },
    { key: "review", label: "Review", sub: settled ? (remaining ? `${remaining} to review` : "All approved") : "After the run", state: settled ? (remaining ? "attn" : "done") : "todo", onClick: () => go("review"), disabled: !settled },
    { key: "export", label: "Export", sub: schema ? schema.output_format.toUpperCase() : "", state: settled && !remaining ? "done" : "todo", onClick: () => go("export"), disabled: !settled },
  ];
  return (
    <Shell crumb={id} steps={steps} current={view} right={job ? <StatusChip status={job.status} /> : null}>
      <ErrorNote error={error} />
      {!job && !error && <div className="card empty">Loading job…</div>}
      {job && view === "run" && <RunView job={job} onReview={() => go("review")} />}
      {job && view === "review" && (schema ? <ReviewView job={job} schema={schema} items={items} docs={docs} reload={loadReview} onExport={() => go("export")} />
        : <div className="card empty">The review opens when the run has finished.</div>)}
      {job && view === "export" && schema && <ExportView job={job} remaining={remaining} defaultFormat={schema.output_format} onReview={() => go("review")} />}
    </Shell>
  );
}

export default function Page() {
  return <Suspense><JobPage /></Suspense>;
}
