import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FolderPicker } from "@/components/FolderPicker";
import { ProcessingOptions } from "@/components/ProcessingOptions";
import { TagInput } from "@/components/TagInput";
import { Textarea } from "@/components/ui/textarea";
import { todayIso } from "@/lib/dates";
import type { ProcessingValues } from "@/lib/processing";
import type { ScanPhase } from "@/lib/scanWizard";

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
  processing,
  onProcessingChange,
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
  processing: ProcessingValues;
  onProcessingChange: (v: ProcessingValues) => void;
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
        <FolderPicker id="scan-folder" value={fields.folderId} onChange={(folderId) => onChange({ folderId })} />
      </div>
      <div>
        <Label htmlFor="scan-tags">Tags</Label>
        <TagInput id="scan-tags" value={fields.tagIds} onChange={(tagIds) => onChange({ tagIds })} />
      </div>
      <ProcessingOptions idPrefix="scan" value={processing} onChange={onProcessingChange} />
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
