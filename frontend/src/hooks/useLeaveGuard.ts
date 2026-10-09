import { useEffect, useRef } from "react";
import { useBlocker } from "react-router";
import { isExternalNavigation } from "@/lib/agentLaunch";

/** beforeunload handler: ask before leaving, except while an origami-agent link opens. */
export function guardBeforeUnload(event: BeforeUnloadEvent): void {
  if (isExternalNavigation()) return;
  event.preventDefault();
  event.returnValue = ""; // browsers show their own fixed "Leave site?" text
}

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
    window.addEventListener("beforeunload", guardBeforeUnload);
    return () => window.removeEventListener("beforeunload", guardBeforeUnload);
  }, [active]);
}
