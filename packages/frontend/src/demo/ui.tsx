// Public demo UI: the slim banner, the read-only toasts and the voice note.
import { useEffect, useRef } from 'react';
import { Info, Mic, PlayCircle } from 'lucide-react';
import { useToast } from '../components/Toast';
import {
  DEMO_BANNER,
  DEMO_EVENT,
  notifyReadOnly,
  VOICE_VIDEO_URL,
} from './mode';

/** "Read-only demo · fictional company … · synthetic data", above every page. */
export function DemoBanner() {
  return (
    <div
      role="note"
      data-testid="demo-banner"
      className="relative z-30 flex h-7 flex-none items-center justify-center gap-2 border-b border-amber-300/60 bg-amber-100/90 px-3 text-[11px] font-medium text-amber-900 dark:border-amber-500/30 dark:bg-amber-500/15 dark:text-amber-200"
    >
      <Info className="h-3.5 w-3.5 flex-none" aria-hidden="true" />
      <span className="truncate">{DEMO_BANNER}</span>
    </div>
  );
}

/** Shows the read-only notes the demo API sends (at most one every 1.5 s). */
export function DemoToaster() {
  const { showToast } = useToast();
  const last = useRef(0);
  useEffect(() => {
    const onNote = (e: Event) => {
      const now = Date.now();
      if (now - last.current < 1500) return;
      last.current = now;
      showToast('info', String((e as CustomEvent).detail ?? ''), 6000);
    };
    window.addEventListener(DEMO_EVENT, onNote);
    return () => window.removeEventListener(DEMO_EVENT, onNote);
  }, [showToast]);
  return null;
}

/** In place of the live microphone: what voice does, and the recording. */
export function VoiceDemoNote({ compact = false }: { compact?: boolean }) {
  const hasVideo = /^https?:\/\//.test(VOICE_VIDEO_URL);
  return (
    <div
      className={`flex items-start gap-2 text-left ${compact ? 'px-4 py-2.5 text-xs' : 'rounded-xl border border-white/40 bg-white/40 p-3 text-xs dark:border-slate-700 dark:bg-slate-800/60'} text-slate-600 dark:text-slate-300`}
      data-testid="voice-demo-note"
    >
      <Mic className="mt-0.5 h-4 w-4 flex-none text-purple-500" aria-hidden="true" />
      <span>
        <span className="font-medium text-slate-700 dark:text-slate-200">
          Voice bot
        </span>{' '}
        {compact
          ? '— the live microphone is off in this read-only demo.'
          : '— the live microphone is off in this read-only demo. The voice bot talks with the customer in Hindi, English or Hinglish about the documents their file still needs, says it is an AI and that the call is recorded, and never promises approval.'}{' '}
        <a
          href={VOICE_VIDEO_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex items-center gap-1 font-medium text-blue-600 hover:underline dark:text-blue-400"
          data-voice-video={hasVideo ? 'ready' : 'placeholder'}
          onClick={(e) => {
            if (hasVideo) return;
            e.preventDefault();
            notifyReadOnly('The voice bot video link is added here soon.');
          }}
        >
          <PlayCircle className="h-3.5 w-3.5" aria-hidden="true" />
          Watch the voice bot video
        </a>
      </span>
    </div>
  );
}
