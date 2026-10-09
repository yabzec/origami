import { useCallback, useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { runBatch, type BatchDeps, type BatchItem, type BatchOptions, type ItemState } from "@/lib/batchUpload";

const deps: BatchDeps = {
  ensurePath: (parentId, segments) =>
    api
      .post<{ folder_id: number | null }>("/api/folders/ensure-path", { parent_id: parentId, segments })
      .then((r) => r.folder_id),
  upload: (form, onProgress) => api.upload("/api/documents/upload", form, onProgress),
};

export function useBatchUpload() {
  const qc = useQueryClient();
  const [states, setStates] = useState<Record<string, ItemState>>({});
  const [running, setRunning] = useState(false);
  const controller = useRef<AbortController | null>(null);

  const cancel = useCallback(() => controller.current?.abort(), []);
  useEffect(() => cancel, [cancel]); // stop uploads when the dialog unmounts

  const run = useCallback(
    async (items: BatchItem[], options: BatchOptions) => {
      const ctrl = new AbortController();
      controller.current = ctrl;
      setRunning(true);
      setStates((s) => ({ ...s, ...Object.fromEntries(items.map((i) => [i.key, { status: "queued" } as ItemState])) }));
      try {
        await runBatch(items, options, deps, (key, state) => setStates((s) => ({ ...s, [key]: state })), undefined, ctrl.signal);
      } finally {
        setRunning(false);
        qc.invalidateQueries({ queryKey: ["documents"] });
        qc.invalidateQueries({ queryKey: ["folders"] });
      }
    },
    [qc],
  );

  const reset = useCallback(() => setStates({}), []);
  return { states, running, run, reset, cancel };
}
