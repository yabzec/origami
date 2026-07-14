import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { useMutation } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { STATUS_VARIANTS } from "@/components/DocumentCard";
import { useFolders } from "@/hooks/useFolders";
import { useTags } from "@/hooks/useTags";
import { api } from "@/lib/api";
import { splitHighlights } from "@/lib/snippets";
import type { SearchResponse } from "@/lib/types";

function Snippet({ text }: { text: string }) {
  return (
    <>
      {splitHighlights(text).map((part, index) =>
        part.highlighted ? (
          <mark key={index} className="rounded bg-yellow-200 px-0.5">
            {part.text}
          </mark>
        ) : (
          <span key={index}>{part.text}</span>
        ),
      )}
    </>
  );
}

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [mode, setMode] = useState<"hybrid" | "semantic" | "keyword">("hybrid");
  const [folderId, setFolderId] = useState<number | null>(null);
  const [tagId, setTagId] = useState<number | null>(null);
  const [docType, setDocType] = useState<string | null>(null);
  const { data: folders } = useFolders();
  const { data: tags } = useTags();

  const search = useMutation({
    mutationFn: () =>
      api.post<SearchResponse>("/api/search", {
        query,
        mode,
        filters: {
          folder_id: folderId,
          tag_ids: tagId !== null ? [tagId] : [],
          doc_type: docType,
        },
        limit: 10,
      }),
  });

  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    if (query.trim()) search.mutate();
  };

  return (
    <div className="p-6">
      <h2 className="mb-4 text-lg font-semibold">Search</h2>
      <form onSubmit={onSubmit} className="mb-6 flex flex-wrap items-center gap-2">
        <Input
          className="max-w-md"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search your documents…"
        />
        <Select className="w-32" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
          <option value="hybrid">Hybrid</option>
          <option value="semantic">Semantic</option>
          <option value="keyword">Keyword</option>
        </Select>
        <Select
          className="w-36"
          value={folderId ?? ""}
          onChange={(e) => setFolderId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">All folders</option>
          {(folders ?? []).map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </Select>
        <Select
          className="w-32"
          value={tagId ?? ""}
          onChange={(e) => setTagId(e.target.value ? Number(e.target.value) : null)}
        >
          <option value="">All tags</option>
          {(tags ?? []).map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </Select>
        <Select className="w-28" value={docType ?? ""} onChange={(e) => setDocType(e.target.value || null)}>
          <option value="">All types</option>
          {["scan", "pdf", "text", "image", "video"].map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </Select>
        <Button type="submit" disabled={search.isPending}>
          {search.isPending ? "Searching…" : "Search"}
        </Button>
      </form>

      {search.data && search.data.results.length === 0 && <p className="text-zinc-400">No results.</p>}
      <div className="space-y-4">
        {search.data?.results.map((result) => (
          <div key={result.document.id} className="rounded-lg border border-zinc-200 bg-white p-4">
            <div className="mb-1 flex items-center gap-2">
              <Link to={`/documents/${result.document.id}`} className="font-medium underline">
                {result.document.title}
              </Link>
              <Badge variant={STATUS_VARIANTS[result.document.status]}>{result.document.doc_type}</Badge>
            </div>
            <ul className="space-y-1">
              {result.snippets.map((snippet) => (
                <li key={snippet.chunk_id} className="text-sm text-zinc-600">
                  {snippet.page_number != null && (
                    <span className="mr-1 text-xs text-zinc-400">p. {snippet.page_number}</span>
                  )}
                  <Snippet text={snippet.text} />
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  );
}
