// Public demo build (VITE_PUBLIC_DEMO=1): no login, a fixed demo identity, every
// API call answered in the browser from snapshots of the real app (synthetic
// data), and nothing can be changed. See src/demo/api.ts.

export const PUBLIC_DEMO = import.meta.env.VITE_PUBLIC_DEMO === '1';

export const DEMO_USERNAME = 'asha.verma';
export const DEMO_DISPLAY_NAME = 'Asha Verma';

/** The voice bot video: VITE_VOICE_VIDEO_URL at build time, else the placeholder (deploy.sh can fill it in). */
export const VOICE_VIDEO_URL: string =
  import.meta.env.VITE_VOICE_VIDEO_URL || '{VOICE_VIDEO_URL}';

export const DEMO_BANNER =
  'Read-only demo · fictional company Varunika Loan Partners · synthetic data';

export const READ_ONLY_MESSAGE =
  'Read-only demo: uploads, edits, deletes, erasing and sending to the CRM are switched off here. Everything shown was captured from the real app running on synthetic data.';

export const DEMO_EVENT = 'demo:readonly';

/** A friendly read-only notice (shown as a toast by DemoToaster). */
export function notifyReadOnly(message: string = READ_ONLY_MESSAGE): void {
  if (typeof window === 'undefined') return;
  window.dispatchEvent(new CustomEvent(DEMO_EVENT, { detail: message }));
}

/** Thrown by the demo API for anything that would change data. */
export class DemoReadOnlyError extends Error {
  constructor(message: string = READ_ONLY_MESSAGE) {
    super(message);
    this.name = 'DemoReadOnlyError';
  }
}
