export type CopyOutcome = 'copied' | 'manual';

/**
 * Copies text with the Clipboard API; where it is unavailable or refused
 * (insecure context, permissions), selects the text in `fallback` and tries
 * the legacy copy command. 'manual': the text is selected, the user has to
 * press Ctrl+C.
 */
export async function copyText(
  text: string,
  fallback?: HTMLInputElement | HTMLTextAreaElement | null,
): Promise<CopyOutcome> {
  try {
    if (!navigator.clipboard?.writeText) throw new Error('no clipboard API');
    await navigator.clipboard.writeText(text);
    return 'copied';
  } catch {
    if (!fallback) return 'manual';
    fallback.focus();
    fallback.select();
    try {
      return document.execCommand('copy') ? 'copied' : 'manual';
    } catch {
      return 'manual';
    }
  }
}
