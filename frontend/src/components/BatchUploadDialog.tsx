import { useMemo, useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Label } from "@/components/ui/label";
import { FolderPicker } from "@/components/FolderPicker";
import { ProcessingOptions } from "@/components/ProcessingOptions";
import { TagInput } from "@/components/TagInput";
import { useBatchUpload } from "@/hooks/useBatchUpload";
import { useLeaveGuard } from "@/hooks/useLeaveGuard";
import { batchCounts, planBatch, type ItemState, type PickedFile } from "@/lib/batchUpload";
import { defaultProcessing, type ProcessingValues } from "@/lib/processing";

const LEAVE_MESSAGE = "Uploads are still running. Stop them and close?";

function stateLabel(state: ItemState | undefined): string {
  if (!state) return "";
  if (state.status === "uploading") return `${state.percent}%`;
  if (state.status === "failed") return `Failed: ${state.message}`;
  return state.status === "done" ? "Done" : "Queued";
}

function sizeLabel(bytes: number): string {
  return bytes >= 1_048_576 ? `${(bytes / 1_048_576).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function BatchUploadDialog({
  picked,
  open,
  onClose,
  initialFolderId,
}: {
  picked: PickedFile[] | null;
  open: boolean;
  onClose: () => void;
  initialFolderId?: number | null;
}) {
  const plan = useMemo(() => planBatch(picked ?? []), [picked]);
  const [folderId, setFolderId] = useState<number | null>(initialFolderId ?? null);
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [processing, setProcessing] = useState<ProcessingValues>(defaultProcessing);
  const batch = useBatchUpload();
  const started = Object.keys(batch.states).length > 0;
  const counts = batchCounts(plan.items, batch.states);

  useLeaveGuard(batch.running, LEAVE_MESSAGE, batch.cancel);

  if (!picked) return null;

  const options = { folderId, tagIds, processing };
  const start = () => void batch.run(plan.items, options);
  const retryFailed = () =>
    void batch.run(plan.items.filter((i) => batch.states[i.key]?.status === "failed"), options);
  const close = () => {
    if (batch.running) {
      if (!window.confirm(LEAVE_MESSAGE)) return;
      batch.cancel();
    }
    batch.reset();
    onClose();
  };
  const fileWord = (n: number) => `${n} file${n === 1 ? "" : "s"}`;

  return (
    <Dialog open={open} onClose={close} title={`Upload ${fileWord(plan.items.length)}`}>
      <div className="space-y-3">
        <ul className="max-h-48 space-y-1 overflow-y-auto text-sm">
          {plan.items.map((item) => (
            <li key={item.key} className="flex justify-between gap-2">
              <span className="truncate">{item.relativePath}</span>
              <span className="shrink-0 text-zinc-500">
                {started ? stateLabel(batch.states[item.key]) : sizeLabel(item.file.size)}
              </span>
            </li>
          ))}
          {plan.skipped.map((s) => (
            <li key={`skip:${s.relativePath}`} className="text-zinc-400">
              {s.relativePath} — skipped: {s.reason}
            </li>
          ))}
        </ul>
        {started ? (
          <p className="text-sm" role="status">
            {counts.done} of {counts.total} uploaded{counts.failed > 0 ? `, ${counts.failed} failed` : ""}
          </p>
        ) : (
          <>
            <div>
              <Label htmlFor="bu-folder">Folder</Label>
              <FolderPicker id="bu-folder" value={folderId} onChange={setFolderId} />
            </div>
            <div>
              <Label htmlFor="bu-tags">Tags</Label>
              <TagInput id="bu-tags" value={tagIds} onChange={setTagIds} />
            </div>
            <ProcessingOptions idPrefix="bu" value={processing} onChange={setProcessing} />
            <p className="text-xs text-zinc-500">
              Each file keeps its name as title and its last-modified date as document date.
            </p>
          </>
        )}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={close}>
            {batch.running ? "Stop and close" : started ? "Close" : "Cancel"}
          </Button>
          {!started && (
            <Button onClick={start} disabled={plan.items.length === 0}>
              Upload {fileWord(plan.items.length)}
            </Button>
          )}
          {started && !batch.running && counts.failed > 0 && <Button onClick={retryFailed}>Retry failed</Button>}
        </div>
      </div>
    </Dialog>
  );
}
