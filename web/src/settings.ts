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

// ---------------- atalho para TV ----------------

/** O que o atalho do pen-drive pede para tocar (gravado pela página /tv do servidor, ver tv.html). */
export interface TvPlay {
  source: string;
  password: string;
  plain: boolean;
  config: Partial<Config>;
}

const TV_KEY = "youtube-cipher-studio.tv";

/** Lê e apaga o pedido do atalho para TV: só vale para a primeira abertura da página. */
export function takeTvPlay(): TvPlay | null {
  try {
    const raw = sessionStorage.getItem(TV_KEY);
    sessionStorage.removeItem(TV_KEY);
    return raw ? (JSON.parse(raw) as TvPlay) : null;
  } catch {
    return null;
  }
}

/** Endereço que faz login e abre o vídeo; os dados vão no fragmento (#), que não chega ao servidor. */
export function tvShortcutUrl(origin: string, accessPassword: string, play: TvPlay | null): string {
  const params = new URLSearchParams();
  if (accessPassword) params.set("a", accessPassword);
  if (play) {
    params.set("v", play.source);
    if (play.plain) {
      params.set("m", "plain");
    } else {
      params.set("s", play.password);
      const { grid, invert_colors, scramble_audio } = play.config;
      if (grid) params.set("g", grid);
      if (invert_colors !== undefined) params.set("i", invert_colors ? "1" : "0");
      if (scramble_audio !== undefined) params.set("au", scramble_audio ? "1" : "0");
    }
  }
  return `${origin}/tv#${params.toString()}`;
}

/** Arquivo HTML autônomo (para o pen-drive) que só redireciona para o endereço acima. */
export function tvShortcutHtml(url: string, title: string): string {
  const attr = (text: string) => text.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;");
  const js = JSON.stringify(url).replace(/</g, "\\u003c");
  return `<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="referrer" content="no-referrer">
<title>${attr(title)}</title>
</head>
<body style="margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;background:#111;color:#eee;font:24px/1.4 Helvetica,Arial,sans-serif;text-align:center">
<p>Abrindo o YouTube Cipher Studio…<br><br><a href="${attr(url)}" style="color:#8b5cf6">Clique aqui se não abrir sozinho</a></p>
<script>location.replace(${js});</script>
</body>
</html>
`;
}
