import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, ApiError } from "@/lib/api";
import { emptyRow, normalizeProperties } from "@/lib/properties";
import type { Document, Property } from "@/lib/types";

const FRAME = "max-h-64 overflow-y-auto rounded border border-zinc-200";

export function PropertiesPanel({ doc }: { doc: Pick<Document, "id" | "properties"> }) {
  const qc = useQueryClient();
  const [rows, setRows] = useState<Property[] | null>(null); // null = view mode
  const [error, setError] = useState<string | null>(null);
  const save = useMutation({
    mutationFn: (properties: Property[]) => api.patch(`/api/documents/${doc.id}`, { properties }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["document", doc.id] });
      qc.invalidateQueries({ queryKey: ["documents"] });
      setRows(null);
    },
  });

  const startEdit = () => {
    setRows(doc.properties.length > 0 ? doc.properties.map((p) => ({ ...p })) : [emptyRow()]);
    setError(null);
    save.reset();
  };
  const cancel = () => {
    setRows(null);
    setError(null);
    save.reset();
  };
  const update = (index: number, change: Partial<Property>) =>
    setRows((current) => current!.map((row, i) => (i === index ? { ...row, ...change } : row)));
  const submit = () => {
    const result = normalizeProperties(rows!);
    setError(result.error);
    if (!result.error) save.mutate(result.rows);
  };

  if (rows === null) {
    return (
      <div>
        <div className="mb-1 flex items-center justify-between">
          <span className="text-sm font-medium">Properties</span>
          <Button variant="outline" size="sm" onClick={startEdit}>
            Edit
          </Button>
        </div>
        <div data-testid="properties-frame" className={`${FRAME} bg-zinc-50 p-3 text-xs`}>
          {doc.properties.length === 0 ? (
            <p className="text-zinc-400">No properties</p>
          ) : (
            <dl className="space-y-1">
              {doc.properties.map((p) => (
                <div key={p.key} className="flex gap-2">
                  <dt className="w-24 shrink-0 break-words font-medium text-zinc-500">{p.key}</dt>
                  <dd className="min-w-0 break-words">{p.value}</dd>
                </div>
              ))}
            </dl>
          )}
        </div>
      </div>
    );
  }

  return (
    <div>
      <span className="mb-1 block text-sm font-medium">Properties</span>
      <div data-testid="properties-frame" className={FRAME}>
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-zinc-50 text-left text-zinc-500">
            <tr>
              <th className="px-1 py-1 font-medium">Key</th>
              <th className="px-1 py-1 font-medium">Value</th>
              <th className="w-6" />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i}>
                <td className="p-1">
                  <Input aria-label="Key" className="h-7 text-xs" value={row.key} onChange={(e) => update(i, { key: e.target.value })} />
                </td>
                <td className="p-1">
                  <Input aria-label="Value" className="h-7 text-xs" value={row.value} onChange={(e) => update(i, { value: e.target.value })} />
                </td>
                <td className="p-1 text-center">
                  <button
                    type="button"
                    aria-label={`Delete row ${row.key || i + 1}`}
                    onClick={() => setRows(rows.filter((_, j) => j !== i))}
                    className="cursor-pointer text-zinc-400 hover:text-red-600"
                  >
                    ×
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {(error || save.isError) && (
        <p className="mt-1 text-xs text-red-600">
          {error ?? (save.error instanceof ApiError ? save.error.message : "Save failed")}
        </p>
      )}
      <div className="mt-2 flex gap-2">
        <Button variant="outline" size="sm" onClick={() => setRows([...rows, emptyRow()])}>
          Add row
        </Button>
        <span className="flex-1" />
        <Button variant="outline" size="sm" onClick={cancel}>
          Cancel
        </Button>
        <Button size="sm" onClick={submit} disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save"}
        </Button>
      </div>
    </div>
  );
}
