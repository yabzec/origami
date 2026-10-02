import { useEffect, useRef } from "react";
import { useBlocker } from "react-router";

/** Confirm before leaving: in-app navigation via useBlocker, reload/close via beforeunload. */
export function useLeaveGuard(active: boolean, message: string, onLeave: () => void): void {
  const onLeaveRef = useRef(onLeave);
  onLeaveRef.current = onLeave;

  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => active && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    if (blocker.state !== "blocked") return;
    if (window.confirm(message)) {
      onLeaveRef.current();
      blocker.proceed();
    } else {
      blocker.reset();
    }
  }, [blocker, message]);

  useEffect(() => {
    if (!active) return;
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = ""; // browsers show their own fixed "Leave site?" text
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [active]);
}
