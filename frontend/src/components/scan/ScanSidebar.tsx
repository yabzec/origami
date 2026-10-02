import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { todayIso } from "@/lib/dates";
import type { ScanPhase } from "@/lib/scanWizard";
import type { Folder, Tag } from "@/lib/types";

export interface ScanFormFields {
  title: string;
  description: string;
  documentDate: string;
  folderId: number | null;
  tagIds: number[];
}

export function emptyScanForm(): ScanFormFields {
  return { title: "", description: "", documentDate: todayIso(), folderId: null, tagIds: [] };
}

export function ScanSidebar({
  fields,
  onChange,
  folders,
  tags,
  phase,
  pageCount,
  previewing,
  onPreview,
  onScan,
  onFinish,
  onDiscard,
}: {
  fields: ScanFormFields;
  onChange: (patch: Partial<ScanFormFields>) => void;
  folders: Folder[];
  tags: Tag[];
  phase: ScanPhase;
  pageCount: number;
  previewing: boolean;
  onPreview: () => void;
  onScan: () => void;
  onFinish: () => void;
  onDiscard: () => void;
}) {
  const busy = phase !== "ready" || previewing;
  return (
    <aside className="w-full space-y-3 lg:w-72">
      <div>
        <Label htmlFor="scan-title">Title</Label>
        <Input id="scan-title" value={fields.title} onChange={(e) => onChange({ title: e.target.value })} />
      </div>
      <div>
        <Label htmlFor="scan-desc">Description</Label>
        <Textarea
          id="scan-desc"
          rows={3}
          value={fields.description}
          onChange={(e) => onChange({ description: e.target.value })}
        />
      </div>
      <div>
        <Label htmlFor="scan-date">Document date</Label>
        <Input
          id="scan-date"
          type="date"
          value={fields.documentDate}
          onChange={(e) => onChange({ documentDate: e.target.value })}
        />
      </div>
      <div>
        <Label htmlFor="scan-folder">Folder</Label>
        <Select
          id="scan-folder"
          value={fields.folderId ?? ""}
          onChange={(e) => onChange({ folderId: e.target.value ? Number(e.target.value) : null })}
        >
          <option value="">(root)</option>
          {folders.map((f) => (
            <option key={f.id} value={f.id}>
              {f.name}
            </option>
          ))}
        </Select>
      </div>
      {tags.length > 0 && (
        <div>
          <Label>Tags</Label>
          <div className="flex flex-wrap gap-2">
            {tags.map((tag) => (
              <label key={tag.id} className="flex items-center gap-1 text-sm">
                <input
                  type="checkbox"
                  checked={fields.tagIds.includes(tag.id)}
                  onChange={(e) =>
                    onChange({
                      tagIds: e.target.checked
                        ? [...fields.tagIds, tag.id]
                        : fields.tagIds.filter((x) => x !== tag.id),
                    })
                  }
                />
                {tag.name}
              </label>
            ))}
          </div>
        </div>
      )}
      <div className="space-y-2 border-t border-zinc-200 pt-3">
        <Button variant="outline" className="w-full" disabled={busy} onClick={onPreview}>
          {previewing ? "Previewing…" : "Preview"}
        </Button>
        <Button className="w-full" disabled={busy} onClick={onScan}>
          {phase === "scanning" ? "Scanning…" : pageCount === 0 ? "Scan first page" : "Scan next page"}
        </Button>
        <Button className="w-full" disabled={busy || pageCount === 0 || !fields.title.trim()} onClick={onFinish}>
          {phase === "compiling" ? "Saving…" : "Finish & save"}
        </Button>
        <Button
          variant="ghost"
          className="w-full"
          disabled={phase === "scanning" || phase === "compiling" || previewing}
          onClick={onDiscard}
        >
          Discard
        </Button>
      </div>
    </aside>
  );
}
