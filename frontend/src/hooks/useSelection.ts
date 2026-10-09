import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { rangeSelect, toggleId } from "@/lib/selection";

/** Selected document ids; cleared when `resetKey` (view + filters) changes. Callbacks are stable. */
export function useSelection(order: string[], resetKey: string) {
  const [raw, setRaw] = useState<Set<string>>(() => new Set());
  const anchor = useRef<string | null>(null);
  const orderRef = useRef(order);
  orderRef.current = order;

  useEffect(() => {
    setRaw(new Set());
    anchor.current = null;
  }, [resetKey]);

  // ids no longer listed (deleted, moved away) never count as selected
  const orderKey = order.join(",");
  const selected = useMemo(() => {
    const visible = new Set(orderRef.current);
    return new Set([...raw].filter((id) => visible.has(id)));
    // eslint-disable-next-line react-hooks/exhaustive-deps -- orderKey is the content key of orderRef.current
  }, [raw, orderKey]);

  const toggle = useCallback((id: string, shift: boolean) => {
    const from = anchor.current; // read now: the updater below runs later, after the anchor moved
    setRaw((prev) => (shift ? rangeSelect(prev, orderRef.current, from, id) : toggleId(prev, id)));
    anchor.current = id;
  }, []);
  const selectAll = useCallback(() => setRaw(new Set(orderRef.current)), []);
  const clear = useCallback(() => {
    setRaw(new Set());
    anchor.current = null;
  }, []);

  return { selected, toggle, selectAll, clear };
}
