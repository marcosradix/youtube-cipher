// Linha do tempo clicável/arrastável com balão de tempo, nos mesmos cinco estilos do desktop.
import { useEffect, useRef, useState } from "react";
import type { ReactNode } from "react";
import { TIMELINE_STYLES, formatTime } from "../settings";

const PAD = 16;
const TRACK_Y = 31;
const HEIGHT = 46;
const GRADIENT = ["#22d3ee", "#8b5cf6", "#ec4899"];

function mix(c1: string, c2: string, t: number): string {
  const a = [1, 3, 5].map((i) => parseInt(c1.slice(i, i + 2), 16));
  const b = [1, 3, 5].map((i) => parseInt(c2.slice(i, i + 2), 16));
  return "#" + a.map((x, i) => Math.round(x + (b[i] - x) * t).toString(16).padStart(2, "0")).join("");
}

function gradientAt(t: number): string {
  const v = Math.min(1, Math.max(0, t)) * (GRADIENT.length - 1);
  const i = Math.min(Math.floor(v), GRADIENT.length - 2);
  return mix(GRADIENT[i], GRADIENT[i + 1], v - i);
}

/** Barra com pontas totalmente arredondadas. */
function Pill({ x1, x2, y, thick, color }: { x1: number; x2: number; y: number; thick: number; color: string }) {
  if (x2 - x1 < 1) return <circle cx={x1} cy={y} r={thick / 2} fill={color} />;
  return <line x1={x1} y1={y} x2={x2} y2={y} stroke={color} strokeWidth={thick} strokeLinecap="round" />;
}

interface Props {
  position: number;
  duration: number;
  buffered: number;
  styleKey: string;
  onSeek: (seconds: number) => void;
}

export default function Timeline({ position, duration, buffered, styleKey, onSeek }: Props) {
  const ref = useRef<SVGSVGElement>(null);
  const [width, setWidth] = useState(600);
  const [hoverX, setHoverX] = useState<number | null>(null);
  const [dragFrac, setDragFrac] = useState<number | null>(null);
  const style = styleKey in TIMELINE_STYLES ? styleKey : "pill";
  const accent = TIMELINE_STYLES[style][1];

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const observer = new ResizeObserver(() => setWidth(el.clientWidth));
    observer.observe(el);
    setWidth(el.clientWidth);
    return () => observer.disconnect();
  }, []);

  const fracAt = (clientX: number) => {
    const rect = ref.current!.getBoundingClientRect();
    return Math.min(1, Math.max(0, (clientX - rect.left - PAD) / Math.max(1, rect.width - 2 * PAD)));
  };
  const localX = (clientX: number) => clientX - ref.current!.getBoundingClientRect().left;

  const x1 = PAD;
  const x2 = Math.max(PAD + 10, width - PAD);
  const y = TRACK_Y;
  const frac = dragFrac ?? (duration > 0 ? Math.min(1, position / duration) : 0);
  const xp = x1 + (x2 - x1) * frac;
  const bufferedFrac = duration > 0 ? Math.min(1, buffered / duration) : 0;
  const xb = Math.max(xp, x1 + (x2 - x1) * bufferedFrac);
  const active = hoverX !== null || dragFrac !== null;
  const hoverFrac = hoverX !== null ? Math.min(1, Math.max(0, (hoverX - PAD) / (x2 - x1))) : null;

  const parts: ReactNode[] = [];
  const add = (node: ReactNode) => parts.push(<g key={parts.length}>{node}</g>);

  if (style === "pill") {
    const thick = active ? 10 : 8;
    add(<Pill x1={x1} x2={x2} y={y} thick={thick} color="#2f2f36" />);
    add(<Pill x1={x1} x2={xb} y={y} thick={thick} color="#55556a" />);
    add(<Pill x1={x1} x2={xp} y={y} thick={thick} color={accent} />);
    add(<circle cx={xp} cy={y} r={active ? 9 : 7} fill="#fff" stroke={accent} strokeWidth={3} />);
  } else if (style === "capsule") {
    add(<Pill x1={x1} x2={x2} y={y} thick={active ? 18 : 16} color="#1f3a3a" />);
    add(<Pill x1={x1} x2={xb} y={y} thick={8} color="#3f6b67" />);
    add(<Pill x1={x1} x2={Math.max(x1, xp)} y={y} thick={8} color={accent} />);
    const handle = active ? 11 : 9;
    add(<line x1={xp} y1={y - handle} x2={xp} y2={y + handle} stroke="#fff" strokeWidth={6} strokeLinecap="round" />);
  } else if (style === "segments") {
    const gap = 5;
    const n = Math.max(8, Math.min(40, Math.floor((x2 - x1) / 30)));
    const seg = ((x2 - x1) - gap * (n - 1)) / n;
    const thick = active ? 8 : 6;
    const inset = thick / 2;
    const hoverSeg = hoverFrac !== null ? Math.floor(hoverFrac * n) : null;
    for (let i = 0; i < n; i++) {
      const sx = x1 + i * (seg + gap);
      const ex = sx + seg;
      const lit = i === hoverSeg;
      const base = lit ? "#6a6a6a" : xb >= ex ? "#5a5248" : "#3a3a3a";
      add(<Pill x1={sx + inset} x2={ex - inset} y={y} thick={thick} color={base} />);
      if (xp >= ex) add(<Pill x1={sx + inset} x2={ex - inset} y={y} thick={thick} color={lit ? "#fbbf24" : accent} />);
      else if (xp > sx) add(<Pill x1={sx + inset} x2={Math.max(sx + inset, xp - inset)} y={y} thick={thick} color={accent} />);
    }
  } else if (style === "dots") {
    const spacing = 9;
    const n = Math.floor((x2 - x1) / spacing) + 1;
    for (let i = 0; i < n; i++) {
      const x = x1 + i * spacing;
      if (x <= xp) add(<circle cx={x} cy={y} r={3} fill={accent} />);
      else if (x <= xb) add(<circle cx={x} cy={y} r={2.6} fill="#8a6a7c" />);
      else add(<circle cx={x} cy={y} r={2.2} fill="#3e3e3e" />);
    }
    add(<circle cx={xp} cy={y} r={active ? 11 : 9} fill="#3a1a2c" />);
    add(<circle cx={xp} cy={y} r={6} fill={accent} stroke="#fff" strokeWidth={2} />);
  } else {
    const thick = active ? 9 : 6;
    add(<Pill x1={x1} x2={x2} y={y} thick={thick} color="#2a2a2a" />);
    add(<Pill x1={x1} x2={xb} y={y} thick={thick} color="#4a4a58" />);
    if (xp > x1) {
      add(
        <>
          <defs>
            <linearGradient id="tl-grad" gradientUnits="userSpaceOnUse" x1={x1} x2={x2} y1={0} y2={0}>
              {GRADIENT.map((c, i) => <stop key={c} offset={i / (GRADIENT.length - 1)} stopColor={c} />)}
            </linearGradient>
          </defs>
          <line x1={x1} y1={y} x2={xp} y2={y} stroke="url(#tl-grad)" strokeWidth={thick} strokeLinecap="round" />
        </>,
      );
    }
    add(<circle cx={xp} cy={y} r={active ? 8 : 6} fill={gradientAt(frac)} stroke="#fff" strokeWidth={2} />);
  }

  let bubble: ReactNode = null;
  if (active && duration > 0) {
    const bx = hoverX ?? xp;
    const text = formatTime(Math.min(1, Math.max(0, (bx - PAD) / (x2 - x1))) * duration, duration);
    const half = 6 + 4 * text.length;
    const cx = Math.min(Math.max(bx, PAD + half - 8), width - PAD - half + 8);
    bubble = (
      <g>
        <rect x={cx - half} y={2} width={2 * half} height={18} rx={9} fill="#f1f1f1" />
        <text x={cx} y={15} textAnchor="middle" fill="#111" fontSize={11} fontWeight="bold"
              fontFamily="Helvetica, Arial, sans-serif">{text}</text>
      </g>
    );
  }

  return (
    <svg
      ref={ref}
      className="timeline"
      height={HEIGHT}
      role="slider"
      aria-label="Linha do tempo"
      aria-valuemin={0}
      aria-valuemax={Math.round(duration)}
      aria-valuenow={Math.round(position)}
      onPointerMove={(e) => {
        setHoverX(localX(e.clientX));
        if (dragFrac !== null) setDragFrac(fracAt(e.clientX));
      }}
      onPointerLeave={() => dragFrac === null && setHoverX(null)}
      onPointerDown={(e) => {
        if (duration <= 0) return;
        e.currentTarget.setPointerCapture(e.pointerId);
        setDragFrac(fracAt(e.clientX));
      }}
      onPointerUp={(e) => {
        if (dragFrac === null) return;
        const target = fracAt(e.clientX) * duration;
        setDragFrac(null);
        if (e.pointerType !== "mouse") setHoverX(null);
        onSeek(target);
      }}
      onPointerCancel={() => setDragFrac(null)}
    >
      {parts}
      {bubble}
    </svg>
  );
}
