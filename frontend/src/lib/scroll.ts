/** True when the scroll position is within `threshold` px of the bottom (auto-scroll stays on). */
export function isNearBottom(scrollTop: number, scrollHeight: number, clientHeight: number, threshold = 40): boolean {
  return scrollHeight - scrollTop - clientHeight <= threshold;
}
