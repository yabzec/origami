import { Link } from "react-router";
import { DocTypeIcon } from "@/components/DocTypeIcon";
import { Badge } from "@/components/ui/badge";
import { formatDate } from "@/lib/dates";
import type { Document } from "@/lib/types";

export const STATUS_VARIANTS = {
  ready: "green",
  processing: "blue",
  pending: "amber",
  failed: "red",
} as const;

export function DocumentCard({ doc, onDelete }: { doc: Document; onDelete: (id: string) => void }) {
  return (
    <div className="group relative rounded-lg border border-zinc-200 bg-white p-4 hover:shadow">
      <Link to={`/documents/${doc.id}`} className="block">
        <div className="mb-2 flex items-center gap-2">
          <DocTypeIcon doc={doc} />
          <span className="flex-1 truncate font-medium">{doc.title}</span>
          <Badge variant={STATUS_VARIANTS[doc.status]} title={doc.error_message ?? undefined}>
            {doc.status}
          </Badge>
        </div>
        <div className="flex flex-wrap gap-1">
          {doc.tags.map((tag) => (
            <span
              key={tag.id}
              className="rounded-full px-2 py-0.5 text-xs"
              style={{ backgroundColor: `${tag.color}22`, color: tag.color }}
            >
              {tag.name}
            </span>
          ))}
        </div>
        <p className="mt-2 text-xs text-zinc-400">
          {formatDate(doc.document_date)}
          {doc.page_count ? ` · ${doc.page_count} pages` : ""}
          {doc.file_size ? ` · ${Math.round(doc.file_size / 1024)} KB` : ""}
        </p>
      </Link>
      <button
        className="absolute right-2 bottom-2 hidden text-zinc-300 hover:text-red-600 group-hover:block"
        title="Delete"
        onClick={() => window.confirm(`Delete "${doc.title}"?`) && onDelete(doc.id)}
      >
        🗑
      </button>
    </div>
  );
}
