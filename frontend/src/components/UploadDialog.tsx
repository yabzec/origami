import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { FolderPicker } from "@/components/FolderPicker";
import { useTags } from "@/hooks/useTags";
import { useUploadDocument } from "@/hooks/useDocuments";
import { ApiError } from "@/lib/api";
import { buildUploadForm, fileStem } from "@/lib/upload";
import { OcrLanguageSelect } from "@/components/OcrLanguageSelect";
import { DEFAULT_OCR_LANGUAGES } from "@/lib/ocrLanguages";
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
  const { data: tags } = useTags();
  const upload = useUploadDocument();
  const [title, setTitle] = useState("");
  const [folderId, setFolderId] = useState<number | null>(initialFolderId ?? null);
  const [tagIds, setTagIds] = useState<number[]>([]);
  const [languages, setLanguages] = useState(DEFAULT_OCR_LANGUAGES);
  const [ocrEnabled, setOcrEnabled] = useState(true);
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
        ocrLanguages: languages,
        ocrEnabled,
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
          <Label>Tags</Label>
          <div className="flex flex-wrap gap-2">
            {(tags ?? []).map((tag) => (
              <label key={tag.id} className="flex items-center gap-1 text-sm">
                <input
                  type="checkbox"
                  checked={tagIds.includes(tag.id)}
                  onChange={(e) =>
                    setTagIds(e.target.checked ? [...tagIds, tag.id] : tagIds.filter((id) => id !== tag.id))
                  }
                />
                {tag.name}
              </label>
            ))}
          </div>
        </div>
        <div>
          <label className="flex items-center gap-2 text-sm">
            <input type="checkbox" checked={ocrEnabled} onChange={(e) => setOcrEnabled(e.target.checked)} />
            Run OCR (extract text)
          </label>
        </div>
        {ocrEnabled && (
          <div>
            <Label htmlFor="up-lang">OCR language</Label>
            <OcrLanguageSelect id="up-lang" value={languages} onChange={(e) => setLanguages(e.target.value)} />
          </div>
        )}
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
