import { useState, type FormEvent } from "react";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useCreateFolder } from "@/hooks/useFolders";
import { ApiError } from "@/lib/api";

export function NewFolderDialog({
  open,
  parentId,
  onClose,
}: {
  open: boolean;
  parentId: number | null;
  onClose: () => void;
}) {
  const [name, setName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const create = useCreateFolder();

  const close = () => {
    setName("");
    setError(null);
    onClose();
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Enter a folder name");
      return;
    }
    create.mutate(
      { name: trimmed, parent_id: parentId },
      {
        onSuccess: close,
        onError: (err) => setError(err instanceof ApiError ? err.message : "Could not create the folder"),
      },
    );
  };

  return (
    <Dialog open={open} onClose={close} title="New folder">
      <form onSubmit={submit} className="space-y-3">
        <Input
          autoFocus
          aria-label="Folder name"
          value={name}
          onChange={(e) => {
            setName(e.target.value);
            setError(null);
          }}
        />
        {error && <p className="text-sm text-red-600">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button type="button" variant="outline" onClick={close}>
            Cancel
          </Button>
          <Button type="submit" disabled={create.isPending}>
            Create
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
