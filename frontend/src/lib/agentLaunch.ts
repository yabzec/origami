const EXTERNAL_NAVIGATION_MS = 1000;

let externalNavigation = false;
let externalTimer: ReturnType<typeof setTimeout> | undefined;

/** True right after openAgentUrl: the browser fires beforeunload for the external link. */
export function isExternalNavigation(): boolean {
  return externalNavigation;
}

/** Hands an origami-agent:// link to the OS. The page does not navigate for external schemes. */
export function openAgentUrl(url: string): void {
  externalNavigation = true;
  clearTimeout(externalTimer);
  externalTimer = setTimeout(() => {
    externalNavigation = false;
  }, EXTERNAL_NAVIGATION_MS);
  window.location.href = url;
}
