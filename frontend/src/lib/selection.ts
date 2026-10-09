export function toggleId(sel: ReadonlySet<string>, id: string): Set<string> {
  const next = new Set(sel);
  if (next.has(id)) next.delete(id);
  else next.add(id);
  return next;
}

/** Shift-click: add everything between the anchor and `id` (in list order). */
export function rangeSelect(
  sel: ReadonlySet<string>,
  order: string[],
  anchor: string | null,
  id: string,
): Set<string> {
  const from = anchor === null ? -1 : order.indexOf(anchor);
  const to = order.indexOf(id);
  if (from === -1 || to === -1) return toggleId(sel, id);
  const [lo, hi] = from < to ? [from, to] : [to, from];
  return new Set([...sel, ...order.slice(lo, hi + 1)]);
}

/** Esc clears the selection, unless it is closing an open dialog (dialogs listen on window too). */
export function shouldClearOnEscape(doc: Document = document): boolean {
  return doc.querySelector('[role="dialog"]') === null;
}
