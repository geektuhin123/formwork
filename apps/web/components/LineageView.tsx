"use client";
import { useMemo, useState } from "react";
import { Item, LineageEdge, LineageGraph, MAX_LINEAGE_SOURCES, Tone, fmt } from "@/lib/api";

type Props = {
  graph: LineageGraph;
  recordKeys: string[];
  recordKey: string;
  onRecordChange: (key: string) => void;
  selId: string | null;
  onSelect: (id: string) => void;
  filter: "all" | "review" | "conflict";
  itemsById: Map<string, Item>;
};

const SRC_X = 16, REC_X = 300, FLD_X = 560, NODE_W = 170, FIELD_W = 190, ROW_H = 46, NODE_H = 40, CONTENT_W = 766;
const open = (t: Tone) => t === "review" || t === "conflict" || t === "missing";

function layout(graph: LineageGraph) {
  const sources = graph.nodes.filter((n) => n.kind === "source");
  const fields = graph.nodes.filter((n) => n.kind === "field");
  const rows = Math.max(sources.length, fields.length, 1);
  const height = rows * ROW_H + 36;
  const pos = new Map<string, { x: number; y: number; w: number }>();
  sources.forEach((n, i) => pos.set(n.id, { x: SRC_X, y: 18 + i * ROW_H + ((rows - sources.length) * ROW_H) / 2, w: NODE_W }));
  fields.forEach((n, i) => pos.set(n.id, { x: FLD_X, y: 18 + i * ROW_H, w: FIELD_W }));
  pos.set("record", { x: REC_X, y: height / 2 - NODE_H / 2, w: NODE_W });
  return { pos, height };
}

const bezier = (x1: number, y1: number, x2: number, y2: number) => {
  const mx = (x1 + x2) / 2;
  return `M${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2} ${y2}`;
};

export function LineageView({ graph, recordKeys, recordKey, onRecordChange, selId, onSelect, filter, itemsById }: Props) {
  const [focusSource, setFocusSource] = useState<string | null>(null);
  const { pos, height } = useMemo(() => layout(graph), [graph]);
  const focusField = graph.nodes.find((n) => n.kind === "field" && n.itemId === selId)?.id ?? null;
  const hasFocus = Boolean(focusField || focusSource);
  const edgeOn = (e: LineageEdge) => (focusSource ? e.from === `src:${focusSource}` : focusField ? e.to === focusField : false);

  return (
    <div>
      {recordKeys.length > 1 && (
        <div className="rec-tabs" role="tablist" aria-label="Record">
          {recordKeys.map((k) => (
            <button key={k} type="button" role="tab" aria-pressed={k === recordKey} data-testid="lineage-record-tab" onClick={() => onRecordChange(k)}>
              {k}
            </button>
          ))}
        </div>
      )}
      <div className="lineage" data-testid="lineage-graph" style={{ height }}>
        <svg className="ln-svg" viewBox={`0 0 ${CONTENT_W} ${height}`} preserveAspectRatio="none" aria-hidden="true">
          {graph.edges.map((e) => {
            const a = pos.get(e.from), b = pos.get(e.to);
            if (!a || !b) return null;
            const x1 = a.x + a.w, y1 = a.y + NODE_H / 2, x2 = b.x, y2 = b.y + NODE_H / 2;
            const cls = `ln-edge ${e.role}${hasFocus ? (edgeOn(e) ? " on" : " dim") : ""}`;
            return <path key={e.id} className={cls} d={bezier(x1, y1, x2, y2)} />;
          })}
        </svg>
        {graph.nodes.map((n) => {
          const p = pos.get(n.id);
          if (!p) return null;
          if (n.kind === "record") {
            return (
              <div key={n.id} className="ln-rec" style={{ left: p.x, top: p.y }}>
                <span className="fname">{n.label}</span><span className="sub">{n.sub}</span>
              </div>
            );
          }
          if (n.kind === "source") {
            const docId = n.id.slice("src:".length);
            return (
              <button key={n.id} type="button" className={`ln-src${focusSource === docId ? " on" : ""}`} style={{ left: p.x, top: p.y }}
                data-testid="lineage-source" onClick={() => setFocusSource(focusSource === docId ? null : docId)}>
                <span className="fname">{n.label}</span><span className="sub">{n.sub}</span>
              </button>
            );
          }
          const item = n.itemId ? itemsById.get(n.itemId) : undefined;
          const dim = filter !== "all" && (filter === "review" ? !open(n.tone!) : n.tone !== "conflict");
          const label = n.rows !== undefined ? `${n.rows} row${n.rows === 1 ? "" : "s"}` : item ? (item.kind === "gap" ? "missing" : fmt(item.value)) : "—";
          const pct = item ? Math.round(item.confidence * 100) : 0;
          return (
            <button key={n.id} type="button" className={`cell st-${n.tone ?? "missing"}${dim ? " dim" : ""}`} style={{ left: p.x, top: p.y, width: FIELD_W }}
              aria-pressed={n.itemId === selId} data-testid="cell" data-status={n.tone} data-field={n.label}
              aria-label={`${n.label}: ${label}`} onClick={() => n.itemId && onSelect(n.itemId)}>
              <span className="dot" /><span className="val">{label}</span>
              <span className="cb"><i style={{ width: `${n.rows !== undefined ? 100 : pct}%` }} /></span>
            </button>
          );
        })}
      </div>
      {graph.truncatedSources > 0 && (
        <div className="lineage-note">Showing the {MAX_LINEAGE_SOURCES} most-cited sources — switch to Grid for the rest.</div>
      )}
    </div>
  );
}
