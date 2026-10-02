import { useEffect, useRef, useState, useCallback } from 'react';
import { createPortal } from 'react-dom';
import {
  X,
  Workflow,
  Code,
  Eye,
  ZoomIn,
  ZoomOut,
  RotateCcw,
} from 'lucide-react';
import mermaid from 'mermaid';

mermaid.initialize({ startOnLoad: false });

function isDarkMode() {
  return document.documentElement.classList.contains('dark');
}

function runMermaid(el: HTMLElement, code: string) {
  mermaid.initialize({
    startOnLoad: false,
    theme: isDarkMode() ? 'dark' : 'default',
    securityLevel: 'loose',
    suppressErrorRendering: true,
  });
  el.textContent = code;
  el.removeAttribute('data-processed');
  return mermaid.run({ nodes: [el] });
}

const MIN_SCALE = 0.1;
const MAX_SCALE = 5;
const SCALE_STEP = 0.25;

interface MermaidBlockProps {
  code: string;
}

export default function MermaidBlock({ code }: MermaidBlockProps) {
  const [showModal, setShowModal] = useState(false);
  const [viewMode, setViewMode] = useState<'diagram' | 'code'>('diagram');
  const [error, setError] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const viewportRef = useRef<HTMLDivElement>(null);
  const codeRef = useRef(code);
  codeRef.current = code;

  // Zoom & pan state
  const [scale, setScale] = useState(1);
  const [position, setPosition] = useState({ x: 0, y: 0 });
  const [isDragging, setIsDragging] = useState(false);
  const dragStartRef = useRef({ x: 0, y: 0 });

  // Fit diagram to viewport
  const fitToView = useCallback(() => {
    const svg = containerRef.current?.querySelector('svg');
    const vp = viewportRef.current?.getBoundingClientRect();
    if (!svg || !vp) return;

    // Measure SVG at scale=1 by dividing out the current scale
    const rect = svg.getBoundingClientRect();
    const currentScale = scale || 1;
    const naturalW = rect.width / currentScale;
    const naturalH = rect.height / currentScale;
    if (naturalW <= 0 || naturalH <= 0) return;

    const padding = 48;
    const fitScale = Math.min(
      (vp.width - padding) / naturalW,
      (vp.height - padding) / naturalH,
    );
    setScale(Math.max(fitScale, MIN_SCALE));
    setPosition({ x: 0, y: 0 });
  }, [scale]);

  const handleZoomIn = useCallback(() => {
    setScale((prev) => Math.min(prev + SCALE_STEP, MAX_SCALE));
  }, []);

  const handleZoomOut = useCallback(() => {
    setScale((prev) => Math.max(prev - SCALE_STEP, MIN_SCALE));
  }, []);

  const handleWheel = useCallback((e: React.WheelEvent) => {
    e.preventDefault();
    const delta = e.deltaY > 0 ? -SCALE_STEP : SCALE_STEP;
    setScale((prev) => Math.min(Math.max(prev + delta, MIN_SCALE), MAX_SCALE));
  }, []);

  const handleMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (viewMode !== 'diagram') return;
      setIsDragging(true);
      dragStartRef.current = {
        x: e.clientX - position.x,
        y: e.clientY - position.y,
      };
    },
    [viewMode, position],
  );

  const handleMouseMove = useCallback(
    (e: React.MouseEvent) => {
      if (!isDragging) return;
      setPosition({
        x: e.clientX - dragStartRef.current.x,
        y: e.clientY - dragStartRef.current.y,
      });
    },
    [isDragging],
  );

  const handleMouseUp = useCallback(() => {
    setIsDragging(false);
  }, []);

  // Render mermaid after modal mounts or when switching back to diagram view
  useEffect(() => {
    if (!showModal || viewMode !== 'diagram' || !containerRef.current) return;

    setError(false);
    runMermaid(containerRef.current, codeRef.current)
      .then(() => {
        // After mermaid renders, fit the SVG to the viewport
        requestAnimationFrame(() => {
          const svg = containerRef.current?.querySelector('svg');
          const vp = viewportRef.current?.getBoundingClientRect();
          if (!svg || !vp) return;

          const rect = svg.getBoundingClientRect();
          if (rect.width <= 0 || rect.height <= 0) return;

          const padding = 48;
          const fitScale = Math.min(
            (vp.width - padding) / rect.width,
            (vp.height - padding) / rect.height,
          );
          setScale(Math.max(fitScale, 0.5));
          setPosition({ x: 0, y: 0 });
        });
      })
      .catch(() => {
        setError(true);
      });
  }, [showModal, viewMode]);

  // Re-render on theme change while modal is open in diagram view
  useEffect(() => {
    if (!showModal || viewMode !== 'diagram' || !containerRef.current) return;
    const el = containerRef.current;

    const observer = new MutationObserver(() => {
      runMermaid(el, codeRef.current).catch(() => undefined);
    });
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class'],
    });
    return () => observer.disconnect();
  }, [showModal, viewMode]);

  // Keyboard shortcuts
  useEffect(() => {
    if (!showModal) return;
    const handler = (e: KeyboardEvent) => {
      switch (e.key) {
        case 'Escape':
          setShowModal(false);
          break;
        case '+':
        case '=':
          handleZoomIn();
          break;
        case '-':
          handleZoomOut();
          break;
        case '0':
          fitToView();
          break;
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, [showModal, handleZoomIn, handleZoomOut, fitToView]);

  const handleOpen = useCallback(() => {
    setViewMode('diagram');
    setScale(1);
    setPosition({ x: 0, y: 0 });
    setShowModal(true);
  }, []);

  return (
    <>
      <div className="my-3 flex items-center gap-3 px-4 py-3 rounded-xl border border-indigo-200 dark:border-indigo-500/30 bg-gradient-to-r from-indigo-50/80 to-violet-50/80 dark:from-indigo-900/20 dark:to-violet-900/20">
        <div className="flex items-center justify-center w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 shadow-sm flex-shrink-0">
          <Workflow className="w-4 h-4 text-white" />
        </div>
        <span className="flex-1 text-sm font-medium text-slate-700 dark:text-slate-300">
          Mermaid Diagram
        </span>
        <button
          type="button"
          onClick={handleOpen}
          className="px-3 py-1.5 text-xs font-medium rounded-lg bg-indigo-500 hover:bg-indigo-600 text-white transition-colors shadow-sm"
        >
          View
        </button>
      </div>

      {showModal &&
        createPortal(
          <div className="fixed inset-0 z-50 flex items-center justify-center">
            {/* Backdrop */}
            <div
              className="absolute inset-0 bg-black/55 dark:bg-black/65 backdrop-blur-md"
              onClick={() => setShowModal(false)}
            />

            {/* Modal */}
            <div className="upload-modal-container relative rounded-2xl w-full max-w-5xl mx-4 h-[85vh] flex flex-col overflow-hidden border border-white/70 dark:border-indigo-500/20 shadow-[0_25px_60px_-15px_rgba(0,0,0,0.15)] dark:shadow-[0_0_80px_rgba(99,102,241,0.08),0_25px_50px_-12px_rgba(0,0,0,0.5)]">
              {/* Gradient glow */}
              <div
                className="dark:hidden absolute inset-0 pointer-events-none rounded-2xl"
                style={{
                  background:
                    'radial-gradient(ellipse 60% 50% at 80% 0%, rgba(99, 140, 241, 0.1) 0%, transparent 70%)',
                }}
              />
              <div
                className="hidden dark:block absolute inset-0 pointer-events-none rounded-2xl"
                style={{
                  background:
                    'radial-gradient(ellipse 60% 50% at 80% 0%, rgba(99, 102, 241, 0.15) 0%, transparent 70%)',
                }}
              />

              {/* Header */}
              <div className="relative flex items-center justify-between px-4 py-3 border-b border-black/[0.06] dark:border-[#2a2f45] flex-shrink-0">
                <div className="flex items-center gap-2.5">
                  <div className="flex items-center justify-center w-7 h-7 rounded-lg bg-gradient-to-br from-indigo-500 to-violet-600 shadow-sm">
                    <Workflow className="w-3.5 h-3.5 text-white" />
                  </div>
                  <span className="text-sm font-semibold text-[#1e293b] dark:text-[#f8fafc]">
                    Mermaid Diagram
                  </span>
                </div>

                <div className="flex items-center gap-2">
                  {/* Toggle: Diagram / Code */}
                  <div className="flex items-center rounded-lg border border-black/10 dark:border-[#3b4264] overflow-hidden">
                    <button
                      type="button"
                      onClick={() => setViewMode('diagram')}
                      className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors ${
                        viewMode === 'diagram'
                          ? 'bg-blue-600 dark:bg-indigo-600 text-white'
                          : 'text-[#64748b] hover:bg-white/40 dark:hover:bg-[#1e2235]'
                      }`}
                    >
                      <Eye className="w-3.5 h-3.5" />
                      Diagram
                    </button>
                    <button
                      type="button"
                      onClick={() => setViewMode('code')}
                      className={`inline-flex items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium transition-colors ${
                        viewMode === 'code'
                          ? 'bg-blue-600 dark:bg-indigo-600 text-white'
                          : 'text-[#64748b] hover:bg-white/40 dark:hover:bg-[#1e2235]'
                      }`}
                    >
                      <Code className="w-3.5 h-3.5" />
                      Code
                    </button>
                  </div>

                  {/* Zoom controls (diagram mode only) */}
                  {viewMode === 'diagram' && !error && (
                    <>
                      <div className="w-px h-5 bg-black/10 dark:bg-[#3b4264]" />
                      <div className="flex items-center gap-0.5">
                        <button
                          type="button"
                          onClick={handleZoomOut}
                          disabled={scale <= MIN_SCALE}
                          className="p-1.5 rounded-lg hover:bg-white/40 dark:hover:bg-[#1e2235] transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                          title="Zoom out (-)"
                        >
                          <ZoomOut className="w-4 h-4 text-[#64748b]" />
                        </button>
                        <span className="min-w-[3.5rem] text-center text-xs font-medium text-[#64748b]">
                          {Math.round(scale * 100)}%
                        </span>
                        <button
                          type="button"
                          onClick={handleZoomIn}
                          disabled={scale >= MAX_SCALE}
                          className="p-1.5 rounded-lg hover:bg-white/40 dark:hover:bg-[#1e2235] transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                          title="Zoom in (+)"
                        >
                          <ZoomIn className="w-4 h-4 text-[#64748b]" />
                        </button>
                        <button
                          type="button"
                          onClick={fitToView}
                          className="p-1.5 rounded-lg hover:bg-white/40 dark:hover:bg-[#1e2235] transition-colors"
                          title="Fit to view (0)"
                        >
                          <RotateCcw className="w-4 h-4 text-[#64748b]" />
                        </button>
                      </div>
                    </>
                  )}

                  <div className="w-px h-5 bg-black/10 dark:bg-[#3b4264]" />
                  <button
                    type="button"
                    onClick={() => setShowModal(false)}
                    className="p-1.5 hover:bg-white/40 dark:hover:bg-[#1e2235] rounded-lg transition-colors"
                  >
                    <X className="h-5 w-5 text-[#64748b]" />
                  </button>
                </div>
              </div>

              {/* Content */}
              <div
                ref={viewportRef}
                className="relative flex-1 min-h-0 overflow-hidden"
                onWheel={viewMode === 'diagram' ? handleWheel : undefined}
                onMouseDown={
                  viewMode === 'diagram' ? handleMouseDown : undefined
                }
                onMouseMove={handleMouseMove}
                onMouseUp={handleMouseUp}
                onMouseLeave={handleMouseUp}
                style={{
                  cursor:
                    viewMode === 'diagram'
                      ? isDragging
                        ? 'grabbing'
                        : 'grab'
                      : 'default',
                }}
              >
                {viewMode === 'code' ? (
                  <pre className="overflow-auto h-full m-0 rounded-none bg-transparent dark:bg-[#0d1117] p-6 text-sm leading-relaxed">
                    <code className="text-[#334155] dark:text-[#cbd5e1]">
                      {code}
                    </code>
                  </pre>
                ) : error ? (
                  <pre className="overflow-auto h-full m-0 rounded-none bg-transparent dark:bg-[#0d1117] p-6 text-sm leading-relaxed">
                    <code className="text-[#334155] dark:text-[#cbd5e1]">
                      {code}
                    </code>
                  </pre>
                ) : (
                  <div className="w-full h-full flex items-center justify-center">
                    <div
                      ref={containerRef}
                      className="mermaid [&_svg]:max-w-none"
                      style={{
                        transform: `scale(${scale}) translate(${position.x / scale}px, ${position.y / scale}px)`,
                        transition: isDragging
                          ? 'none'
                          : 'transform 0.2s ease-out',
                        transformOrigin: 'center center',
                      }}
                    />
                  </div>
                )}
              </div>

              {/* Footer hint */}
              {viewMode === 'diagram' && !error && (
                <div className="relative flex-shrink-0 px-4 py-2 border-t border-black/[0.06] dark:border-[#2a2f45] text-center">
                  <span className="text-[11px] text-[#94a3b8] dark:text-[#475569]">
                    Scroll to zoom · Drag to pan · 0 to fit · Esc to close
                  </span>
                </div>
              )}
            </div>
          </div>,
          document.body,
        )}
    </>
  );
}
