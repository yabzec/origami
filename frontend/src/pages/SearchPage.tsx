import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { useMutation } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { DocFilters, FilterField, type DocFilterValues } from "@/components/DocFilters";
import { STATUS_VARIANTS } from "@/components/DocumentCard";
import { useFolders } from "@/hooks/useFolders";
import { useTags } from "@/hooks/useTags";
import { api, ApiError } from "@/lib/api";
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
  const [filters, setFilters] = useState<DocFilterValues>({ tagId: null, docType: null, dateFrom: null, dateTo: null });
  const { data: folders } = useFolders();
  const { data: tags } = useTags();

  const search = useMutation({
    mutationFn: () =>
      api.post<SearchResponse>("/api/search", {
        query,
        mode,
        filters: {
          folder_id: folderId,
          tag_ids: filters.tagId !== null ? [filters.tagId] : [],
          doc_type: filters.docType,
          date_from: filters.dateFrom,
          date_to: filters.dateTo,
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
      <form onSubmit={onSubmit} className="mb-6 space-y-4">
        <div className="mx-auto flex max-w-2xl gap-2">
          <Input
            className="flex-1"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search your documents…"
          />
          <Button type="submit" disabled={search.isPending}>
            {search.isPending ? "Searching…" : "Search"}
          </Button>
        </div>
        <DocFilters value={filters} tags={tags ?? []} onChange={(patch) => setFilters({ ...filters, ...patch })}>
          <FilterField label="Mode">
            <Select className="w-32" value={mode} onChange={(e) => setMode(e.target.value as typeof mode)}>
              <option value="hybrid">Hybrid</option>
              <option value="semantic">Semantic</option>
              <option value="keyword">Keyword</option>
            </Select>
          </FilterField>
          <FilterField>
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
          </FilterField>
        </DocFilters>
      </form>

      {search.isError && (
        <div className="mb-3 rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700">
          {search.error instanceof ApiError ? search.error.message : "Search failed"}
        </div>
      )}
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
