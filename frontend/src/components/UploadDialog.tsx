import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FolderPicker } from "@/components/FolderPicker";
import { useUploadDocument } from "@/hooks/useDocuments";
import { ApiError } from "@/lib/api";
import { buildUploadForm, fileStem } from "@/lib/upload";
import { ProcessingOptions } from "@/components/ProcessingOptions";
import { TagInput } from "@/components/TagInput";
import { defaultProcessing, type ProcessingValues } from "@/lib/processing";
import { todayIso } from "@/lib/dates";

export function UploadDialog({
  file,
  open,
  onClose,
  initialFolderId,
}: {
  file: File | null;
  open: boolean;
  onClose: () => void;
  initialFolderId?: number | null;
}) {
  const upload = useUploadDocument();
  const [title, setTitle] = useState("");
  const [folderId, setFolderId] = useState<number | null>(initialFolderId ?? null);
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [processing, setProcessing] = useState<ProcessingValues>(defaultProcessing);
  const [documentDate, setDocumentDate] = useState(todayIso);
  const [error, setError] = useState<string | null>(null);

  if (!file) return null;

  const submit = () => {
    setError(null);
    upload.mutate(
      buildUploadForm(file, {
        title: title || fileStem(file.name),
        folderId,
        tagIds,
        ocrLanguages: processing.ocrLanguages || undefined,
        ocrEnabled: processing.ocrEnabled,
        summaryEnabled: processing.summaryEnabled,
        translationEnabled: processing.translationEnabled,
        documentDate,
      }),
      {
        onSuccess: () => {
          setTitle("");
          setTagIds([]);
          setDocumentDate(todayIso());
          onClose();
        },
        onError: (err) => setError(err instanceof ApiError ? err.message : "Upload failed"),
      },
    );
  };

  return (
    <Dialog open={open} onClose={onClose} title={`Upload ${file.name}`}>
      <div className="space-y-3">
        <div>
          <Label htmlFor="up-title">Title</Label>
          <Input id="up-title" value={title} placeholder={fileStem(file.name)} onChange={(e) => setTitle(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="up-date">Document date</Label>
          <Input id="up-date" type="date" value={documentDate} onChange={(e) => setDocumentDate(e.target.value)} />
        </div>
        <div>
          <Label htmlFor="up-folder">Folder</Label>
          <FolderPicker id="up-folder" value={folderId} onChange={setFolderId} />
        </div>
        <div>
          <Label htmlFor="up-tags">Tags</Label>
          <TagInput id="up-tags" value={tagIds} onChange={setTagIds} />
        </div>
        <ProcessingOptions idPrefix="up" value={processing} onChange={setProcessing} />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={upload.isPending}>
            {upload.isPending ? "Uploading…" : "Upload"}
          </Button>
        </div>
      </div>
    </Dialog>
  );
}
