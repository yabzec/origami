import type { PickedFile } from "./batchUpload";

export function pickedFromInput(files: FileList | File[]): PickedFile[] {
  return Array.from(files).map((file) => ({ file, relativePath: file.webkitRelativePath || file.name }));
}

async function walk(entry: FileSystemEntry, prefix: string, out: PickedFile[]): Promise<void> {
  const path = prefix ? `${prefix}/${entry.name}` : entry.name;
  if (entry.isFile) {
    const file = await new Promise<File>((ok, fail) => (entry as FileSystemFileEntry).file(ok, fail));
    out.push({ file, relativePath: path });
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((ok, fail) => reader.readEntries(ok, fail));
      if (batch.length === 0) break;
      for (const child of batch) await walk(child, path, out);
    }
  }
}

/** Files and folders from a drop; entries are taken synchronously, before the event ends. */
export async function pickedFromDataTransfer(dt: DataTransfer): Promise<PickedFile[]> {
  const entries = Array.from(dt.items)
    .map((item) => (typeof item.webkitGetAsEntry === "function" ? item.webkitGetAsEntry() : null))
    .filter((e): e is FileSystemEntry => e !== null);
  if (entries.length === 0) return pickedFromInput(dt.files);
  const out: PickedFile[] = [];
  for (const entry of entries) await walk(entry, "", out);
  return out;
}
