"use client";
import { useEffect, useRef, useState } from "react";
import { BASE, Doc, Evidence, api } from "@/lib/api";

type Block = { id: string; type: string; text: string; cells?: string[][] | null; locator?: string | null };
const docCache = new Map<string, Promise<{ blocks: Block[] }>>();
const loadDoc = (id: string) => {
  if (!docCache.has(id)) docCache.set(id, api<{ blocks: Block[] }>(`/documents/${id}`));
  return docCache.get(id)!;
};

/** Digital PDFs render with PDF.js; scans and photos use the stored (deskewed) page image the OCR boxes refer to. */
function PdfPage({ fileId, page, onFail }: { fileId: string; page: number; onFail: () => void }) {
  const ref = useRef<HTMLCanvasElement>(null);
  useEffect(() => {
    let cancelled = false;
    let task: { cancel: () => void } | null = null;
    (async () => {
      try {
        const pdfjs = await import("pdfjs-dist");
        pdfjs.GlobalWorkerOptions.workerSrc = new URL("pdfjs-dist/build/pdf.worker.min.mjs", import.meta.url).toString();
        const pdf = await pdfjs.getDocument({ url: `${BASE}/files/${fileId}/content` }).promise;
        const p = await pdf.getPage(page);
        const viewport = p.getViewport({ scale: 1.6 });
        const canvas = ref.current;
        if (!canvas || cancelled) return;
        canvas.width = viewport.width; canvas.height = viewport.height;
        const render = p.render({ canvas, canvasContext: canvas.getContext("2d")!, viewport } as Parameters<typeof p.render>[0]);
        task = render;
        await render.promise;
        canvas.dataset.rendered = "pdfjs";
      } catch (e) {
        if (!cancelled && (e as { name?: string })?.name !== "RenderingCancelledException") onFail();
      }
    })();
    return () => { cancelled = true; task?.cancel(); };
  }, [fileId, page, onFail]);
  return <canvas ref={ref} role="img" aria-label={`Page ${page} of the source document`} />;
}

function rowOf(evLocator: string | undefined, blockLocator: string | null | undefined): number | null {
  const a = /!([A-Z]+)(\d+):/.exec(evLocator ?? ""), b = /!([A-Z]+)(\d+):/.exec(blockLocator ?? "");
  return a && b ? +a[2] - +b[2] : null;
}

export function SourceViewer({ doc, evidence, alt }: { doc: Doc | undefined; evidence: Evidence; alt?: boolean }) {
  const [block, setBlock] = useState<Block | null>(null);
  const [pdfFailed, setPdfFailed] = useState(false);
  const box = useRef<HTMLDivElement>(null);
  const manual = evidence.document_id === "manual";
  useEffect(() => {
    setBlock(null); setPdfFailed(false);
    if (manual || evidence.bbox) return;
    let live = true;
    loadDoc(evidence.document_id).then((d) => live && setBlock(d.blocks.find((b) => b.id === evidence.block_id) ?? null)).catch(() => undefined);
    return () => { live = false; };
  }, [evidence.block_id, evidence.document_id, evidence.bbox, manual]);
  useEffect(() => {
    const view = box.current;
    const el = view?.querySelector(".hl, .hit, mark");
    if (!view || !el) return;  // scroll the viewer only, never the page
    const delta = el.getBoundingClientRect().top - view.getBoundingClientRect().top;
    view.scrollTop += delta - view.clientHeight / 2 + el.getBoundingClientRect().height / 2;
  }, [evidence, block]);

  if (manual) return <div className="viewer" ref={box}><div className="textview">Entered by a reviewer ({evidence.locator?.replace("manual:", "")}). There is no source region for this value.</div></div>;
  if (evidence.bbox && doc) {
    const b = evidence.bbox;
    const usePdf = doc.digital && doc.mime_type === "application/pdf" && !pdfFailed;
    return (
      <div className="viewer" ref={box} data-testid="viewer" data-kind={usePdf ? "pdf" : "image"}>
        <div className="pagebox">
          {usePdf ? <PdfPage fileId={doc.file_id} page={b.page} onFail={() => setPdfFailed(true)} />
            // eslint-disable-next-line @next/next/no-img-element
            : <img src={`${BASE}/documents/${doc.id}/pages/${b.page}`} alt={`Page ${b.page} of ${doc.filename}`} />}
          <div className={`hl ${alt ? "alt" : ""}`} data-testid="highlight" style={{ left: `${b.x0 * 100 - 0.6}%`, top: `${b.y0 * 100 - 0.4}%`, width: `${(b.x1 - b.x0) * 100 + 1.2}%`, height: `${(b.y1 - b.y0) * 100 + 0.8}%` }} />
        </div>
      </div>
    );
  }
  if (block?.cells?.length) {
    const hit = rowOf(evidence.locator, block.locator);
    const start = Math.max(1, (hit ?? 1) - 4), rows = block.cells.slice(start, start + 9);
    return (
      <div className="viewer" ref={box} data-testid="viewer" data-kind="sheet">
        <div className="sheetview"><div className="loc">{evidence.locator}</div>
          <table className="xl"><thead><tr><th className="rn" />{block.cells[0].map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
            <tbody>{rows.map((r, i) => (
              <tr key={i} className={start + i === hit ? "hit" : ""} data-testid={start + i === hit ? "highlight" : undefined}>
                <td className="rn">{start + i + 1}</td>{r.map((c, j) => <td key={j}>{c}</td>)}</tr>))}</tbody></table></div>
      </div>
    );
  }
  const text = block?.text ?? evidence.quote;
  const at = text.indexOf(evidence.quote);
  return (
    <div className="viewer" ref={box} data-testid="viewer" data-kind="text">
      <div className="textview"><div className="loc mono muted" style={{ fontSize: 12, marginBottom: 6 }}>{evidence.locator}</div>
        {at >= 0 ? <>{text.slice(0, at)}<mark data-testid="highlight">{evidence.quote}</mark>{text.slice(at + evidence.quote.length)}</> : text}</div>
    </div>
  );
}
