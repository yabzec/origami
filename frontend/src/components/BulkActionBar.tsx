import { useState } from "react";
import { FolderPicker } from "@/components/FolderPicker";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { itemsLabel } from "@/lib/counts";

export function BulkActionBar({
  folderCount,
  documentCount,
  deleteFolderTotal,
  deleteDocumentTotal,
  disabledFolderIds,
  onSelectAll,
  onMove,
  onDelete,
  onClear,
  busy = false,
}: {
  folderCount: number;
  documentCount: number;
  deleteFolderTotal: number;
  deleteDocumentTotal: number;
  disabledFolderIds: ReadonlySet<number>;
  onSelectAll: () => void;
  onMove: (folderId: number | null) => void;
  onDelete: () => void;
  onClear: () => void;
  busy?: boolean;
}) {
  const [dialog, setDialog] = useState<"move" | "delete" | null>(null);
  const [target, setTarget] = useState<number | null>(null);

  return (
    <>
      <div className="sticky top-0 z-20 mb-3 flex flex-wrap items-center gap-2 rounded-lg border border-zinc-200 bg-white p-2 shadow">
        <span className="px-2 text-sm font-medium">{itemsLabel(folderCount, documentCount)} selected</span>
        <Button variant="ghost" onClick={onSelectAll}>
          Select all
        </Button>
        <Button variant="outline" disabled={busy} onClick={() => {
            setTarget(null);
            setDialog("move");
          }}>
          Move…
        </Button>
        <Button variant="destructive" disabled={busy} onClick={() => setDialog("delete")}>
          Delete
        </Button>
        <Button variant="ghost" className="ml-auto" onClick={onClear}>
          Clear
        </Button>
      </div>
      <Dialog
        open={dialog === "move"}
        onClose={() => setDialog(null)}
        title={`Move ${itemsLabel(folderCount, documentCount, " and ")}`}
      >
        <div className="space-y-3">
          <FolderPicker value={target} onChange={setTarget} disabledIds={disabledFolderIds} />
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setDialog(null)}>
              Cancel
            </Button>
            <Button
              onClick={() => {
                onMove(target);
                setDialog(null);
              }}
            >
              Move here
            </Button>
          </div>
        </div>
      </Dialog>
      <Dialog
        open={dialog === "delete"}
        onClose={() => setDialog(null)}
        title={`Delete ${itemsLabel(deleteFolderTotal, deleteDocumentTotal, " and ")}?`}
      >
        <p className="mb-4 text-sm text-zinc-600">
          {folderCount > 0
            ? "The folders, their subfolders and all their documents are removed permanently, with the files and their extracted text."
            : "The files and their extracted text are removed permanently."}
        </p>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setDialog(null)}>
            Cancel
          </Button>
          <Button
            variant="destructive"
            onClick={() => {
              onDelete();
              setDialog(null);
            }}
          >
            Delete permanently
          </Button>
        </div>
      </Dialog>
    </>
  );
}
