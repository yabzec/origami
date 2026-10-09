import { buildUploadForm, fileStem, uploadProcessingFields } from "./upload";
import type { ProcessingValues } from "./processing";

/** Must match EXTENSION_MAP in backend/app/api/uploads.py. */
export const UPLOAD_EXTENSIONS = [
  ".pdf", ".txt", ".md", ".doc", ".docx", ".odt", ".rtf",
  ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp",
  ".mp4", ".mkv", ".mov", ".avi", ".webm",
];

const SYSTEM_FILES = new Set(["thumbs.db", "desktop.ini"]);

export interface PickedFile {
  file: File;
  relativePath: string; // "Bills/2025/a.pdf" for folder uploads, "a.pdf" otherwise
}

export interface BatchItem {
  key: string;
  file: File;
  relativePath: string;
  title: string;
  documentDate: string;
  folderSegments: string[];
}

export interface SkippedFile {
  relativePath: string;
  reason: string;
}

export type ItemState =
  | { status: "queued" }
  | { status: "uploading"; percent: number }
  | { status: "done" }
  | { status: "failed"; message: string };

export interface BatchDeps {
  ensurePath: (parentId: number | null, segments: string[]) => Promise<number | null>;
  upload: (form: FormData, onProgress: (percent: number) => void) => Promise<unknown>;
}

export interface BatchOptions {
  folderId: number | null;
  tagIds: number[];
  processing: ProcessingValues;
}

export function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot > 0 ? name.slice(dot).toLowerCase() : "";
}

function isHidden(relativePath: string): boolean {
  const parts = relativePath.split("/");
  return parts.some((p) => p.startsWith(".")) || SYSTEM_FILES.has(parts[parts.length - 1].toLowerCase());
}

export function localIsoDate(ms: number): string {
  const d = new Date(ms);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function planBatch(picked: PickedFile[]): { items: BatchItem[]; skipped: SkippedFile[] } {
  const items: BatchItem[] = [];
  const skipped: SkippedFile[] = [];
  picked.forEach(({ file, relativePath }, index) => {
    if (isHidden(relativePath)) {
      skipped.push({ relativePath, reason: "hidden or system file" });
      return;
    }
    const ext = extensionOf(file.name);
    if (!UPLOAD_EXTENSIONS.includes(ext)) {
      skipped.push({ relativePath, reason: `unsupported type ${ext || "(none)"}` });
      return;
    }
    items.push({
      key: `${index}:${relativePath}`,
      file,
      relativePath,
      title: fileStem(file.name),
      documentDate: localIsoDate(file.lastModified),
      folderSegments: relativePath.split("/").slice(0, -1),
    });
  });
  return { items, skipped };
}

export async function runBatch(
  items: BatchItem[],
  options: BatchOptions,
  deps: BatchDeps,
  onState: (key: string, state: ItemState) => void,
  concurrency = 3,
): Promise<void> {
  const folders = new Map<string, Promise<number | null>>();
  const folderFor = (segments: string[]): Promise<number | null> => {
    if (segments.length === 0) return Promise.resolve(options.folderId);
    const key = segments.join("/");
    let pending = folders.get(key);
    if (!pending) {
      pending = deps.ensurePath(options.folderId, segments);
      folders.set(key, pending);
      pending.catch(() => folders.delete(key)); // a retry resolves the folder again
    }
    return pending;
  };

  let next = 0;
  const worker = async () => {
    while (next < items.length) {
      const item = items[next++];
      try {
        const folderId = await folderFor(item.folderSegments);
        onState(item.key, { status: "uploading", percent: 0 });
        const form = buildUploadForm(item.file, {
          title: item.title,
          documentDate: item.documentDate,
          folderId,
          tagIds: options.tagIds,
          ...uploadProcessingFields(options.processing),
        });
        await deps.upload(form, (percent) => onState(item.key, { status: "uploading", percent }));
        onState(item.key, { status: "done" });
      } catch (err) {
        onState(item.key, { status: "failed", message: err instanceof Error ? err.message : "Upload failed" });
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(concurrency, items.length) }, worker));
}

export function batchCounts(items: BatchItem[], states: Record<string, ItemState>) {
  let done = 0;
  let failed = 0;
  for (const item of items) {
    const status = states[item.key]?.status;
    if (status === "done") done++;
    if (status === "failed") failed++;
  }
  return { total: items.length, done, failed };
}
