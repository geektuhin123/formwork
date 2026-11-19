"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { ErrorNote, Shell, StatusChip } from "@/components/Shell";
import { Job, api } from "@/lib/api";

export default function JobsPage() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let live = true;
    const load = () => api<Job[]>("/jobs").then((j) => live && setJobs(j)).catch((e) => live && setError(e.message));
    load();
    const t = setInterval(load, 5000);
    return () => { live = false; clearInterval(t); };
  }, []);
  return (
    <Shell crumb="All jobs" right={<Link className="btn primary" href="/new">New job</Link>}>
      <div className="view-head"><div><h2>Jobs</h2><p>Each job turns a set of source files into one dataset in your target format. Open a job to review flagged fields and export.</p></div></div>
      <ErrorNote error={error} />
      <div className="card">
        {jobs && jobs.length === 0 && <div className="empty">No jobs yet. <Link href="/new">Start one</Link> by choosing a target format and adding sources.</div>}
        {!jobs && !error && <div className="empty">Loading jobs…</div>}
        {jobs && jobs.length > 0 && (
          <div className="scroll-x"><table className="jobs">
            <thead><tr><th>Job</th><th>Status</th><th>Files</th><th>In review</th><th>Auto-approved</th><th>Cost</th><th>Started</th></tr></thead>
            <tbody>{jobs.map((j) => (
              <tr key={j.id}>
                <td><Link href={`/jobs/${j.id}`} className="mono">{j.id}</Link></td>
                <td><StatusChip status={j.status} /></td>
                <td className="num">{j.file_ids.length}</td>
                <td className="num">{j.stats?.in_review ?? "—"}</td>
                <td className="num">{j.stats?.auto_approved ?? "—"}</td>
                <td className="num">${j.cost_usd.toFixed(3)}</td>
                <td className="muted">{j.created_at ? new Date(j.created_at).toLocaleString() : ""}</td>
              </tr>))}</tbody>
          </table></div>
        )}
      </div>
    </Shell>
  );
}
