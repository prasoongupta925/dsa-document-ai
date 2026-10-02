/// <reference types="vite/client" />
declare const __APP_VERSION__: string;

interface ImportMetaEnv {
  /** '1' builds the no-login, read-only public demo (src/demo). */
  readonly VITE_PUBLIC_DEMO?: string;
  /** Public demo: the voice bot video link (else a {VOICE_VIDEO_URL} placeholder). */
  readonly VITE_VOICE_VIDEO_URL?: string;
}
