/** The description is still the AI summary (derived; no stored flag). */
export function isAiDescription(description: string, summary: string | null): boolean {
  return summary !== null && summary.trim() !== "" && description === summary;
}

/** Value for the description field after the server value changed (pipeline or re-process). */
export function nextDescription(current: string, previousServer: string | null, nextServer: string): string {
  return current === previousServer ? nextServer : current;
}
