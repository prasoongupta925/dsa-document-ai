import { useEffect, useRef } from 'react';

const NODES = [
  { x: 0, y: -40, z: 20, color: '#3b82f6', size: 4 },
  { x: 35, y: 20, z: -15, color: '#8b5cf6', size: 3.5 },
  { x: -35, y: 20, z: -15, color: '#10b981', size: 3.5 },
  { x: 20, y: -20, z: -30, color: '#f59e0b', size: 3 },
  { x: -20, y: -20, z: 30, color: '#ef4444', size: 3 },
  { x: 0, y: 35, z: 25, color: '#06b6d4', size: 3 },
  { x: 25, y: 10, z: 25, color: '#ec4899', size: 2.5 },
  { x: -25, y: -10, z: -25, color: '#a855f7', size: 2.5 },
];

const EDGES = [
  [0, 1],
  [0, 2],
  [1, 2],
  [0, 3],
  [1, 5],
  [2, 4],
  [3, 6],
  [4, 7],
  [5, 6],
  [6, 7],
  [3, 5],
  [4, 2],
];

export default function GraphLoading() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext('2d');
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    const w = 200;
    const h = 200;
    canvas.width = w * dpr;
    canvas.height = h * dpr;
    canvas.style.width = `${w}px`;
    canvas.style.height = `${h}px`;
    ctx.scale(dpr, dpr);

    let angle = 0;
    let animId: number;

    const isDark = document.documentElement.classList.contains('dark');

    const draw = () => {
      angle += 0.008;
      ctx.clearRect(0, 0, w, h);

      const cosA = Math.cos(angle);
      const sinA = Math.sin(angle);
      const cosB = Math.cos(angle * 0.7);
      const sinB = Math.sin(angle * 0.7);

      // Project 3D to 2D with rotation
      const projected = NODES.map((n) => {
        const x1 = n.x * cosA - n.z * sinA;
        const z1 = n.x * sinA + n.z * cosA;
        const y1 = n.y * cosB - z1 * sinB;
        const z2 = n.y * sinB + z1 * cosB;
        const scale = 1 + z2 / 120;
        return {
          x: w / 2 + x1 * scale,
          y: h / 2 + y1 * scale,
          z: z2,
          scale,
          color: n.color,
          size: n.size * scale,
        };
      });

      // Sort by z for depth ordering
      const sortedEdges = [...EDGES].sort((a, b) => {
        const za = (projected[a[0]].z + projected[a[1]].z) / 2;
        const zb = (projected[b[0]].z + projected[b[1]].z) / 2;
        return za - zb;
      });

      // Draw edges
      for (const [i, j] of sortedEdges) {
        const a = projected[i];
        const b = projected[j];
        const alpha = 0.1 + ((a.z + b.z) / 2 + 40) / 160;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.strokeStyle = isDark
          ? `rgba(148,163,184,${alpha * 0.4})`
          : `rgba(100,116,139,${alpha * 0.3})`;
        ctx.lineWidth = 0.5 + alpha * 0.5;
        ctx.stroke();
      }

      // Sort nodes by z
      const sortedNodes = projected
        .map((p, i) => ({ ...p, i }))
        .sort((a, b) => a.z - b.z);

      // Draw nodes
      for (const n of sortedNodes) {
        const alpha = 0.4 + (n.z + 40) / 100;
        ctx.beginPath();
        ctx.arc(n.x, n.y, Math.max(1.5, n.size), 0, Math.PI * 2);
        ctx.fillStyle = n.color;
        ctx.globalAlpha = Math.max(0.3, Math.min(1, alpha));
        ctx.fill();
        // Glow
        ctx.beginPath();
        ctx.arc(n.x, n.y, Math.max(2, n.size * 1.8), 0, Math.PI * 2);
        const grad = ctx.createRadialGradient(
          n.x,
          n.y,
          n.size * 0.5,
          n.x,
          n.y,
          n.size * 1.8,
        );
        grad.addColorStop(0, n.color + '40');
        grad.addColorStop(1, n.color + '00');
        ctx.fillStyle = grad;
        ctx.globalAlpha = Math.max(0.2, alpha * 0.6);
        ctx.fill();
      }
      ctx.globalAlpha = 1;

      animId = requestAnimationFrame(draw);
    };

    draw();
    return () => cancelAnimationFrame(animId);
  }, []);

  return (
    <div className="flex flex-col items-center justify-center h-full w-full bg-slate-50 dark:bg-[#1e2235]">
      <canvas ref={canvasRef} className="w-[200px] h-[200px]" />
      <p className="mt-3 text-xs text-slate-400 dark:text-slate-500">
        Loading graph...
      </p>
    </div>
  );
}
