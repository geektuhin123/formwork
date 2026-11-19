"use client";
import { FieldSpec, TargetSchema, WEAK_WORDS, leaves } from "@/lib/api";
import { Icon } from "./Shell";

const TYPES = ["string", "integer", "number", "boolean", "date", "datetime", "enum", "currency"];
const POLICIES = ["prefer_highest_confidence", "prefer_latest_source", "prefer_source_priority", "require_agreement", "sum", "concat_unique"];

export function weakCount(schema: TargetSchema): number {
  return leaves(schema.fields).filter((f) => words(f.description) < WEAK_WORDS).length;
}
const words = (s: string) => s.trim().split(/\s+/).filter(Boolean).length;

function update(fields: FieldSpec[], path: string, patch: Partial<FieldSpec>): FieldSpec[] {
  return fields.map((f) => (f.path === path ? { ...f, ...patch } : f.children?.length ? { ...f, children: update(f.children, path, patch) } : f));
}
function remove(fields: FieldSpec[], path: string): FieldSpec[] {
  return fields.filter((f) => f.path !== path).map((f) => (f.children?.length ? { ...f, children: remove(f.children, path) } : f));
}

export function SchemaEditor({ schema, onChange }: { schema: TargetSchema; onChange: (s: TargetSchema) => void }) {
  const set = (path: string, patch: Partial<FieldSpec>) => onChange({ ...schema, fields: update(schema.fields, path, patch) });
  const rows = leaves(schema.fields);
  const weak = weakCount(schema);
  const addField = () => {
    let n = rows.length + 1;
    while (rows.some((r) => r.path === `field_${n}`)) n++;
    onChange({ ...schema, fields: [...schema.fields, { path: `field_${n}`, type: "string", description: "", required: false, examples: [], rules: [], merge_policy: POLICIES[0], children: [] }] });
  };
  const toggleKey = (path: string) => {
    const has = schema.entity_key.includes(path);
    onChange({ ...schema, entity_key: has ? schema.entity_key.filter((k) => k !== path) : [...schema.entity_key, path] });
  };
  return (
    <div className="stack">
      <div className="card card-pad">
        <h3 style={{ marginBottom: 12 }}>What one record means</h3>
        <div className="facts">
          <label className="fact"><small>Schema name</small>
            <input className="field-input" value={schema.name} onChange={(e) => onChange({ ...schema, name: e.target.value })} aria-label="Schema name" /></label>
          <label className="fact"><small>Record grain</small>
            <input className="field-input" value={schema.record_grain} onChange={(e) => onChange({ ...schema, record_grain: e.target.value })} aria-label="Record grain" /></label>
          <div className="fact"><small>Entity key (identifies one record across sources)</small>
            <b className="mono">{schema.entity_key.join(" + ") || "none - pick at least one field"}</b></div>
          <label className="fact"><small>Output format</small>
            <select className="field-input" value={schema.output_format} onChange={(e) => onChange({ ...schema, output_format: e.target.value as TargetSchema["output_format"] })}>
              <option value="json">JSON</option><option value="csv">CSV</option><option value="xlsx">XLSX</option></select></label>
        </div>
      </div>
      <div className="card">
        <div className="prev-head">
          <h3>Fields <span className="muted" style={{ fontWeight: 400, fontSize: 14 }}>{rows.length}</span></h3>
          <div className="rowx">
            {weak > 0
              ? <span className="chip warn" data-testid="weak-count">{Icon.warn}{weak} weak description{weak === 1 ? "" : "s"}</span>
              : <span className="chip good">{Icon.ok}Descriptions look specific</span>}
            <button type="button" className="btn" onClick={addField}>Add field</button>
          </div>
        </div>
        <div className="scroll-x">
          <table className="ftable">
            <thead><tr><th>Field</th><th>Type</th><th style={{ minWidth: 320 }}>Description (what to look for)</th><th>Required</th><th>Rule</th><th>Merge policy</th><th><span className="sr">Remove</span></th></tr></thead>
            <tbody>
              {rows.map((f) => {
                const n = words(f.description);
                const isKey = schema.entity_key.includes(f.path);
                const nested = f.path.includes("[]") || f.path.includes(".");
                return (
                  <tr key={f.path} data-field={f.path}>
                    <td>
                      <span className="fname">{f.path}</span>
                      {!nested && (
                        <button type="button" className="btn ghost" style={{ minHeight: 24, padding: "0 6px", fontSize: 11 }} aria-pressed={isKey} onClick={() => toggleKey(f.path)}
                          title="Use as entity key">{isKey ? <span className="keymark">KEY</span> : <span className="muted">set key</span>}</button>
                      )}
                    </td>
                    <td><select value={f.type} onChange={(e) => set(f.path, { type: e.target.value })} aria-label={`Type of ${f.path}`}>
                      {TYPES.map((t) => <option key={t}>{t}</option>)}</select></td>
                    <td>
                      <textarea value={f.description} className={n < WEAK_WORDS ? "is-weak" : ""} aria-label={`Description of ${f.path}`}
                        aria-describedby={n < WEAK_WORDS ? `weak-${f.path}` : undefined} onChange={(e) => set(f.path, { description: e.target.value })} />
                      {n < WEAK_WORDS && (
                        <div className="weak" id={`weak-${f.path}`} role="note">{Icon.warn}
                          <span>Weak description ({n} word{n === 1 ? "" : "s"}). Say what the value is, where it appears and how it is labelled.</span></div>
                      )}
                    </td>
                    <td><input type="checkbox" checked={f.required} onChange={(e) => set(f.path, { required: e.target.checked })} aria-label={`${f.path} required`} /></td>
                    <td><input type="text" className="rule" value={f.rules[0] ?? ""} placeholder="e.g. approx(total, a + b)" aria-label={`Rule for ${f.path}`}
                      onChange={(e) => set(f.path, { rules: e.target.value.trim() ? [e.target.value] : [] })} /></td>
                    <td><select value={f.merge_policy} onChange={(e) => set(f.path, { merge_policy: e.target.value })} aria-label={`Merge policy for ${f.path}`}>
                      {POLICIES.map((p) => <option key={p}>{p}</option>)}</select></td>
                    <td><button type="button" className="btn ghost" aria-label={`Remove ${f.path}`} onClick={() => onChange({ ...schema, fields: remove(schema.fields, f.path), entity_key: schema.entity_key.filter((k) => k !== f.path) })}>{Icon.bad}</button></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
