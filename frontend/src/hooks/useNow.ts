import { useEffect, useState } from "react";

/** Current time, refreshed every `intervalMs` so relative labels ("next ≈ 2 min") stay current. */
export function useNow(intervalMs = 15000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(timer);
  }, [intervalMs]);
  return now;
}
