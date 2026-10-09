import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select } from "@/components/ui/select";
import { DOC_TYPES, type Tag } from "@/lib/types";

export interface DocFilterValues {
  tagId: number | null;
  docType: string | null;
  dateFrom: string | null; // YYYY-MM-DD
  dateTo: string | null;
}

/** One control in the filter row; the label sits above it, unlabeled controls line up at the bottom. */
export function FilterField({ label, children }: { label?: string; children: ReactNode }) {
  if (!label) return <div className="shrink-0">{children}</div>;
  return (
    <label className="flex shrink-0 flex-col gap-1">
      <span className="text-xs font-medium text-zinc-500">{label}</span>
      {children}
    </label>
  );
}

/** Tag, type and date-range filters on one centered row; `children` go first (page-specific controls). */
export function DocFilters({
  value,
  tags,
  onChange,
  children,
}: {
  value: DocFilterValues;
  tags: Tag[];
  onChange: (patch: Partial<DocFilterValues>) => void;
  children?: ReactNode;
}) {
  return (
    <div className="flex items-end justify-center-safe gap-2 overflow-x-auto pb-1">
      {children}
      <FilterField>
        <Select
          className="w-40"
          value={value.tagId ?? ""}
          onChange={(e) => onChange({ tagId: e.target.value ? Number(e.target.value) : null })}
        >
          <option value="">All tags</option>
          {tags.map((t) => (
            <option key={t.id} value={t.id}>
              {t.name}
            </option>
          ))}
        </Select>
      </FilterField>
      <FilterField>
        <Select className="w-32" value={value.docType ?? ""} onChange={(e) => onChange({ docType: e.target.value || null })}>
          <option value="">All types</option>
          {DOC_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </Select>
      </FilterField>
      <FilterField label="From">
        <Input
          type="date"
          className="w-40"
          value={value.dateFrom ?? ""}
          onChange={(e) => onChange({ dateFrom: e.target.value || null })}
        />
      </FilterField>
      <FilterField label="To">
        <Input
          type="date"
          className="w-40"
          value={value.dateTo ?? ""}
          onChange={(e) => onChange({ dateTo: e.target.value || null })}
        />
      </FilterField>
      {(value.dateFrom || value.dateTo) && (
        <Button type="button" variant="ghost" className="shrink-0" onClick={() => onChange({ dateFrom: null, dateTo: null })}>
          Clear dates
        </Button>
      )}
    </div>
  );
}
