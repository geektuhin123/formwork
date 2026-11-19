"use client";
import Link from "next/link";

export type StepState = "done" | "attn" | "todo";
export type Step = { key: string; label: string; sub: string; state: StepState; href?: string; onClick?: () => void; disabled?: boolean };

export const Icon = {
  ok: <svg className="status-ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M3 8.5l3.2 3.2L13 5" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>,
  warn: <svg className="status-ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 2l6.5 11.5h-13z" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" /><path d="M8 6.5v3.2M8 11.6v.4" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" /></svg>,
  bad: <svg className="status-ico" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /></svg>,
};

export function Shell({ crumb, steps, current, right, children }: {
  crumb: string; steps?: Step[]; current?: string; right?: React.ReactNode; children: React.ReactNode;
}) {
  return (
    <>
      <header className="top">
        <div className="top-row">
          <Link href="/" className="brand" style={{ textDecoration: "none", color: "inherit" }}>
            <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16M4 5v14M9 5v14M4 12h11M4 19h7" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" /></svg>
            Formwork
          </Link>
          <nav className="crumb" aria-label="Workspace">
            <Link href="/" style={{ color: "inherit" }}>Jobs</Link><span aria-hidden="true">/</span><b>{crumb}</b>
          </nav>
          <span className="spacer" />
          <Link className="btn ghost" href="/schemas">Schemas</Link>
          {right}
        </div>
        {steps && (
          <nav className="steps" aria-label="Job steps">
            {steps.map((s, i) => (
              <button key={s.key} type="button" className={`step ${s.state === "todo" ? "" : s.state}`} aria-current={current === s.key ? "page" : undefined}
                disabled={s.disabled} onClick={s.onClick} style={s.disabled ? { opacity: 0.5, cursor: "default" } : undefined}>
                <span className="n">{s.state === "done" && current !== s.key ? Icon.ok : i + 1}</span>
                <span className="lbl"><b>{s.label}</b><small>{s.sub}</small></span>
              </button>
            ))}
          </nav>
        )}
      </header>
      <main>{children}</main>
    </>
  );
}

export function StatusChip({ status }: { status: string }) {
  const map: Record<string, [string, string, React.ReactNode]> = {
    completed: ["good", "Completed", Icon.ok], needs_review: ["warn", "Needs review", Icon.warn], failed: ["crit", "Failed", Icon.bad],
  };
  const [cls, label, icon] = map[status] ?? ["accent", status.charAt(0).toUpperCase() + status.slice(1), null];
  return <span className={`chip ${cls}`}>{icon}{label}</span>;
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? <div className="alert" role="alert">{Icon.bad}<span>{error}</span></div> : null;
}
