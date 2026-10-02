import { useRef, useEffect } from 'react';

interface BouncingCirclesLoaderProps {
  size?: number;
  circleSize?: number;
  circleCount?: number;
  color?: string;
  speed?: number;
  className?: string;
}

export default function BouncingCirclesLoader({
  size = 100,
  circleSize = 20,
  circleCount = 10,
  color = '#9ca3af',
  speed = 1.2,
  className = '',
}: BouncingCirclesLoaderProps) {
  const injectedRef = useRef(false);

  useEffect(() => {
    if (injectedRef.current) return;
    const id = 'bouncing-circles-keyframes';
    if (!document.getElementById(id)) {
      const style = document.createElement('style');
      style.id = id;
      style.textContent = `
        @keyframes bouncing-circle {
          0%, 80%, 100% { transform: scale(0); opacity: 0; }
          40% { transform: scale(1); opacity: 1; }
        }
      `;
      document.head.appendChild(style);
    }
    injectedRef.current = true;
  }, []);

  const circles = Array.from({ length: circleCount });

  return (
    <div
      className={`relative flex flex-wrap justify-center items-center ${className}`}
      style={{ width: size, height: size }}
    >
      {circles.map((_, i) => (
        <div
          key={i}
          className="rounded-full"
          style={{
            width: circleSize,
            height: circleSize,
            margin: 2,
            backgroundColor: color,
            animation: `bouncing-circle ${speed}s infinite ease-in-out`,
            animationDelay: `${-i * 0.1}s`,
          }}
        />
      ))}
    </div>
  );
}
