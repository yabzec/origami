import { FilePicker } from "@/components/chat/FilePicker";
import { formatDate } from "@/lib/dates";
import type { DocRef } from "@/lib/types";
import { cn } from "@/lib/utils";

function Chip({ doc, auto = false, onRemove }: { doc: DocRef; auto?: boolean; onRemove: (id: string) => void }) {
  return (
    <span
      className={cn(
        "inline-flex max-w-xs items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
        auto ? "border-blue-200 bg-blue-50" : "border-zinc-300 bg-white",
      )}
    >
      <span className="truncate" title={doc.title}>
        {doc.title}
      </span>
      <span className="shrink-0 text-zinc-400">{formatDate(doc.document_date)}</span>
      {auto && (
        <span className="shrink-0 rounded bg-blue-100 px-1 text-[10px] font-semibold text-blue-700 uppercase">auto</span>
      )}
      <button
        type="button"
        aria-label={`Remove ${doc.title}`}
        onClick={() => onRemove(doc.id)}
        className="shrink-0 text-zinc-400 hover:text-zinc-800"
      >
        ×
      </button>
    </span>
  );
}

export function ContextBar({
  pinned,
  auto,
  onPin,
  onRemove,
}: {
  pinned: DocRef[];
  auto: DocRef[];
  onPin: (doc: DocRef) => void;
  onRemove: (id: string) => void;
}) {
  return (
    <div className="border-b border-zinc-200 px-6 py-2">
      <div className="mx-auto flex max-w-3xl flex-wrap items-center gap-2">
        <span className="text-xs font-medium text-zinc-500">Context:</span>
        {pinned.map((doc) => (
          <Chip key={doc.id} doc={doc} onRemove={onRemove} />
        ))}
        {auto.map((doc) => (
          <Chip key={doc.id} doc={doc} auto onRemove={onRemove} />
        ))}
        {pinned.length === 0 && auto.length === 0 && (
          <span className="text-xs text-zinc-400">Files are picked from your question</span>
        )}
        <FilePicker pinnedIds={pinned.map((d) => d.id)} onPick={onPin} />
      </div>
    </div>
  );
}
