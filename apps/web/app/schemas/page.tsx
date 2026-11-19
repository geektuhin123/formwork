"use client";
import { useEffect, useState } from "react";
import { SchemaEditor } from "@/components/SchemaEditor";
import { ErrorNote, Icon, Shell } from "@/components/Shell";
import { TargetSchema, api, put, schemaBody } from "@/lib/api";

type Summary = { id: string; version: number; name: string; field_count: number; weak_descriptions: number };

export default function SchemasPage() {
  const [list, setList] = useState<Summary[] | null>(null);
  const [schema, setSchema] = useState<TargetSchema | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [note, setNote] = useState<string | null>(null);
  const load = () => api<Summary[]>("/schemas").then(setList).catch((e) => setError(e.message));
  useEffect(() => { load(); }, []);
  const open = (id: string) => api<{ schema: TargetSchema }>(`/schemas/${id}`).then((r) => { setSchema(r.schema); setNote(null); }).catch((e) => setError(e.message));
  const save = async () => {
    if (!schema) return;
    setError(null);
    try {
      const r = await put<{ schema: TargetSchema }>(`/schemas/${schema.id}`, schemaBody(schema));
      setSchema(r.schema); setNote(`Saved as version ${r.schema.version}. Jobs already run keep the version they used.`); load();
    } catch (e) { setError(e instanceof Error ? e.message : "Save failed"); }
  };
  return (
    <Shell crumb="Schemas">
      <div className="view-head"><div><h2>Schemas</h2><p>A schema is your target format. Editing one saves a new version; past jobs are never changed.</p></div>
        {schema && <button type="button" className="btn primary" onClick={save}>Save new version</button>}</div>
      <ErrorNote error={error} />
      {note && <div className="done-banner" role="status">{Icon.ok}{note}</div>}
      <div className="card" style={{ marginBottom: 16 }}>
        {list && !list.length && <div className="empty">No schemas yet. Create one when you start a job.</div>}
        {list && list.length > 0 && (
          <table className="jobs"><thead><tr><th>Name</th><th>Version</th><th>Fields</th><th>Description quality</th><th /></tr></thead>
            <tbody>{list.map((s) => (
              <tr key={s.id}><td><b>{s.name}</b> <span className="mono muted">{s.id}</span></td><td className="num">v{s.version}</td><td className="num">{s.field_count}</td>
                <td>{s.weak_descriptions ? <span className="chip warn">{Icon.warn}{s.weak_descriptions} weak</span> : <span className="chip good">{Icon.ok}Specific</span>}</td>
                <td><button type="button" className="btn" onClick={() => open(s.id)}>Edit</button></td></tr>))}</tbody></table>)}
      </div>
      {schema && <SchemaEditor schema={schema} onChange={setSchema} />}
    </Shell>
  );
}
