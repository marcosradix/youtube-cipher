// Configurações do usuário: equivalem ao ~/.youtube_cipher_studio.json do desktop, guardadas no navegador.
import type { Config } from "./api";

const STORAGE_KEY = "youtube-cipher-studio.config";

export const GRID_LABELS: Record<string, string> = {
  "32x18": "32×18 blocos — robusta (60 px em 1080p)",
  "64x36": "64×36 blocos — equilibrada (30 px em 1080p)",
  "80x45": "80×45 blocos — fina (24 px em 1080p, ideal p/ 4K)",
};

// Cada estilo: nome exibido e cor de destaque (também usada nos ícones sobre o vídeo).
export const TIMELINE_STYLES: Record<string, [string, string]> = {
  pill: ["Pílula", "#8b5cf6"],
  capsule: ["Cápsula", "#2dd4bf"],
  segments: ["Segmentos", "#f59e0b"],
  dots: ["Pontos", "#ec4899"],
  gradient: ["Gradiente", "#22d3ee"],
};

export function loadConfig(defaults: Config): Config {
  try {
    const saved = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    const known = Object.fromEntries(Object.entries(saved).filter(([k]) => k in defaults));
    return { ...defaults, ...known };
  } catch {
    return { ...defaults };
  }
}

export function saveConfig(config: Config): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(config));
  } catch {
    /* navegação privada ou armazenamento bloqueado: segue sem salvar */
  }
}

/** Mesmo texto de CipherSettings.describe() no Python. */
export function describeCipher(config: Config): string {
  const [cols, rows] = config.grid.split("x");
  return `grade ${cols}×${rows} · inversão de cor ${config.invert_colors ? "ligada" : "desligada"}` +
    ` · áudio ${config.scramble_audio ? "embaralhado" : "original"}`;
}

export function formatTime(seconds: number, reference = 0): string {
  const total = Math.floor(Math.max(0, seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h || reference >= 3600 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

export function isYoutubeUrl(text: string): boolean {
  return /^https?:\/\//.test(text) && /(youtube\.com|youtu\.be)\//.test(text);
}
