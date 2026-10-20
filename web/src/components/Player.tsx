// Player: vídeo com indicador de carregamento e ícones no centro, linha do tempo e controles.
// Tela cheia mostra só o vídeo; controles e cursor somem após 2,5 s parados.
import { useCallback, useEffect, useRef, useState } from "react";
import type { SessionInfo } from "../api";
import { StreamEngine } from "../player/engine";
import type { PlayerSnapshot } from "../player/engine";
import { TIMELINE_STYLES, formatTime } from "../settings";
import Timeline from "./Timeline";

const HIDE_AFTER_MS = 2500;
const EMPTY: PlayerSnapshot = {
  position: 0, duration: 0, buffered: 0, paused: false, buffering: false, ended: false, started: false,
};

interface Props {
  session: SessionInfo | null;
  format: { name: string; mime: string } | null;
  scramble: boolean;
  bufferSeconds: number;
  timelineStyle: string;
  volume: number;
  shortcutsActive: boolean;
  onStyleChange: (style: string) => void;
  onVolumeChange: (volume: number) => void;
  onError: (message: string) => void;
  onStatus: (snapshot: PlayerSnapshot, dropped: number) => void;
}

function FullscreenIcon({ exit }: { exit: boolean }) {
  // Cantos para fora = entrar, para dentro = sair (como o ícone desenhado do desktop).
  const d = exit
    ? "M8 3v3a2 2 0 0 1-2 2H3 M21 8h-3a2 2 0 0 1-2-2V3 M3 16h3a2 2 0 0 1 2 2v3 M16 21v-3a2 2 0 0 1 2-2h3"
    : "M3 8V5a2 2 0 0 1 2-2h3 M16 3h3a2 2 0 0 1 2 2v3 M21 16v3a2 2 0 0 1-2 2h-3 M8 21H5a2 2 0 0 1-2-2v-3";
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#e8e8e8" strokeWidth="2.6"
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d={d} /></svg>
  );
}

function Spinner({ accent }: { accent: string }) {
  const [step, setStep] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setStep((s) => (s + 1) % 12), 70);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="overlay">
      <svg width="120" height="150" viewBox="-60 -50 120 150" aria-label="Carregando">
        <circle r="46" fill="#000" stroke="#262626" />
        {Array.from({ length: 12 }, (_, i) => {
          // O ponto "cabeça" é o maior e mais claro; a cauda desbota.
          const fade = 1 - ((step - i + 12) % 12) / 12;
          const angle = (2 * Math.PI * i) / 12 - Math.PI / 2;
          return <circle key={i} cx={26 * Math.cos(angle)} cy={26 * Math.sin(angle)} r={1.6 + 3.4 * fade}
                         fill={accent} fillOpacity={0.15 + 0.85 * fade} />;
        })}
        <text y="72" textAnchor="middle" fill="#bbb" fontSize="13" fontFamily="Helvetica, Arial, sans-serif">
          Carregando…
        </text>
      </svg>
    </div>
  );
}

function CenterIcon({ kind, accent }: { kind: "play" | "replay"; accent: string }) {
  return (
    <div className="overlay">
      <svg width="84" height="84" viewBox="-42 -42 84 84" aria-label={kind === "play" ? "Pausado" : "Fim"}>
        <circle r="38" fill="#000" stroke={accent} strokeWidth="3" />
        {kind === "play" ? (
          <polygon points="-11,-16 -11,16 17,0" fill="#fff" stroke="#fff" strokeWidth="4" strokeLinejoin="round" />
        ) : (
          <>
            <path d="M 8 -13.9 A 16 16 0 1 1 -8 -13.9" fill="none" stroke="#fff" strokeWidth="5" />
            <polygon points="4,-23 15,-13 2,-9" fill="#fff" stroke="#fff" strokeWidth="2" strokeLinejoin="round" />
          </>
        )}
      </svg>
    </div>
  );
}

export default function Player(props: Props) {
  const { session, format, timelineStyle, volume, shortcutsActive } = props;
  const containerRef = useRef<HTMLDivElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const engineRef = useRef<StreamEngine | null>(null);
  const [snap, setSnap] = useState<PlayerSnapshot>(EMPTY);
  const [fullscreen, setFullscreen] = useState(false);
  const [controlsVisible, setControlsVisible] = useState(true);
  const hideTimer = useRef<number | null>(null);
  const hoveringBar = useRef(false);
  const clickTimer = useRef<number | null>(null);
  const callbacks = useRef(props);
  callbacks.current = props;
  const accent = (TIMELINE_STYLES[timelineStyle] ?? TIMELINE_STYLES.pill)[1];

  // Cria o motor para cada vídeo aberto.
  useEffect(() => {
    const video = videoRef.current;
    if (!session || !format || !video) {
      setSnap(EMPTY);
      return;
    }
    const engine = new StreamEngine(video, session, format.name, format.mime, props.scramble, props.bufferSeconds);
    engine.volume = callbacks.current.volume;
    engine.onError = (message) => callbacks.current.onError(message);
    engineRef.current = engine;
    engine.start();
    const id = setInterval(() => {
      const s = engine.snapshot();
      setSnap(s);
      callbacks.current.onStatus(s, engine.droppedFrames());
    }, 100);
    return () => {
      clearInterval(id);
      engine.dispose();
      engineRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, format]);

  useEffect(() => {
    if (engineRef.current) engineRef.current.volume = volume;
  }, [volume]);
  useEffect(() => {
    if (engineRef.current) engineRef.current.bufferSeconds = props.bufferSeconds;
  }, [props.bufferSeconds]);

  const togglePause = useCallback(() => engineRef.current?.togglePause(), []);
  const seekRelative = useCallback((delta: number) => engineRef.current?.seekRelative(delta), []);

  const toggleFullscreen = useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void containerRef.current?.requestFullscreen().catch(() => {});
  }, []);

  useEffect(() => {
    const onChange = () => setFullscreen(document.fullscreenElement === containerRef.current);
    document.addEventListener("fullscreenchange", onChange);
    return () => document.removeEventListener("fullscreenchange", onChange);
  }, []);

  // ---------------- controles automáticos na tela cheia ----------------

  const snapRef = useRef(snap);
  snapRef.current = snap;
  const scheduleHide = useCallback(() => {
    if (hideTimer.current !== null) clearTimeout(hideTimer.current);
    hideTimer.current = window.setTimeout(function hide() {
      const s = snapRef.current;
      // Mantém visível com o mouse sobre a barra ou com o vídeo pausado/no fim.
      if (hoveringBar.current || s.paused || s.ended) {
        hideTimer.current = window.setTimeout(hide, HIDE_AFTER_MS);
        return;
      }
      hideTimer.current = null;
      setControlsVisible(false);
    }, HIDE_AFTER_MS);
  }, []);

  const showControls = useCallback(() => {
    setControlsVisible(true);
    scheduleHide();
  }, [scheduleHide]);

  useEffect(() => {
    if (fullscreen) showControls();
    else {
      if (hideTimer.current !== null) clearTimeout(hideTimer.current);
      setControlsVisible(true);
    }
  }, [fullscreen, showControls]);

  const lastMove = useRef<[number, number]>([-1, -1]);
  const onMouseMove = (e: React.MouseEvent) => {
    // Alguns sistemas geram mousemove sem movimento real; só reage a mudança de posição.
    if (!fullscreen || (lastMove.current[0] === e.screenX && lastMove.current[1] === e.screenY)) return;
    lastMove.current = [e.screenX, e.screenY];
    showControls();
  };

  // ---------------- clique e atalhos ----------------

  const onVideoClick = () => {
    if (!session) return;
    if (fullscreen && !controlsVisible) {
      showControls();         // primeiro clique só traz os controles de volta
      return;
    }
    if (fullscreen) showControls();
    // Clique simples pausa; espera um instante para não conflitar com o duplo clique (tela cheia).
    if (clickTimer.current === null) {
      clickTimer.current = window.setTimeout(() => {
        clickTimer.current = null;
        togglePause();
      }, 250);
    }
  };

  const onVideoDoubleClick = () => {
    if (clickTimer.current !== null) {
      clearTimeout(clickTimer.current);
      clickTimer.current = null;
    }
    toggleFullscreen();
  };

  useEffect(() => {
    if (!shortcutsActive) return;
    const onKey = (e: KeyboardEvent) => {
      const el = e.target as HTMLElement;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT")) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      const actions: Record<string, () => void> = {
        " ": togglePause,
        ArrowLeft: () => seekRelative(-10),
        ArrowRight: () => seekRelative(10),
        f: toggleFullscreen,
        F: toggleFullscreen,
      };
      const action = actions[e.key];
      if (!action || (!session && e.key !== "f" && e.key !== "F")) return;
      e.preventDefault();
      if (fullscreen) showControls();
      action();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [shortcutsActive, session, fullscreen, togglePause, seekRelative, toggleFullscreen, showControls]);

  const timeText = `${formatTime(snap.position, snap.duration)} / ${formatTime(snap.duration, snap.duration)}`;
  const pauseText = snap.ended ? "↻" : snap.paused ? "▶" : "⏸";
  const showSpinner = !!session && (!snap.started || snap.buffering) && !snap.paused && !snap.ended;
  const centerIcon = session && !showSpinner ? (snap.ended ? "replay" : snap.paused ? "play" : null) : null;
  const disabled = !session;

  return (
    <div
      ref={containerRef}
      className={`player${fullscreen ? " is-fullscreen" : ""}${fullscreen && !controlsVisible ? " controls-hidden" : ""}`}
      onMouseMove={onMouseMove}
    >
      <div className="video-area" onClick={onVideoClick} onDoubleClick={onVideoDoubleClick}>
        <video ref={videoRef} playsInline preload="auto" />
        {!session && (
          <div className="overlay placeholder">
            Cole um link do YouTube ou escolha um vídeo codificado
            <br />e clique em 'Assistir' para decodificar vídeo e áudio em tempo real.
          </div>
        )}
        {showSpinner && <Spinner accent={accent} />}
        {centerIcon && <CenterIcon kind={centerIcon} accent={accent} />}
      </div>
      <div
        className="controls"
        onMouseEnter={() => (hoveringBar.current = true)}
        onMouseLeave={() => (hoveringBar.current = false)}
      >
        <Timeline position={snap.position} duration={snap.duration} buffered={snap.buffered}
                  styleKey={timelineStyle} onSeek={(t) => engineRef.current?.seek(t)} />
        <div className="controls-row">
          <button className="ctl small" disabled={disabled} onClick={() => seekRelative(-10)} title="Voltar 10 s (←)">⟲10</button>
          <button className="ctl big" disabled={disabled} onClick={togglePause} title="Pausar / continuar (Espaço)">{pauseText}</button>
          <button className="ctl small" disabled={disabled} onClick={() => seekRelative(10)} title="Avançar 10 s (→)">10⟳</button>
          <span className="time">{timeText}</span>
          <span className="spacer" />
          <label className="muted style-select">
            Estilo:
            <select value={timelineStyle} onChange={(e) => props.onStyleChange(e.target.value)}>
              {Object.entries(TIMELINE_STYLES).map(([key, [label]]) => <option key={key} value={key}>{label}</option>)}
            </select>
          </label>
          <span aria-hidden="true">🔊</span>
          <input className="volume" type="range" min={0} max={1} step={0.01} value={volume}
                 aria-label="Volume" onChange={(e) => props.onVolumeChange(Number(e.target.value))} />
          <button className="ctl" onClick={toggleFullscreen} title={fullscreen ? "Sair da tela cheia (Esc)" : "Tela cheia (F)"}>
            <FullscreenIcon exit={fullscreen} />
          </button>
        </div>
      </div>
    </div>
  );
}
