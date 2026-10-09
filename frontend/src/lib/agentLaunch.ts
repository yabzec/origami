/** Hands an origami-agent:// link to the OS. The page does not navigate for external schemes. */
export function openAgentUrl(url: string): void {
  window.location.href = url;
}
