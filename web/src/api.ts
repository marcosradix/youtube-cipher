// Chamadas ao servidor Python (cipher_studio/web/server.py).

export interface Config {
  grid: string;
  invert_colors: boolean;
  scramble_audio: boolean;
  resolution: string;
  crf: number;
  preset: string;
  deblock: boolean;
  yt_quality: string;
  volume: number;
  timeline_style: string;
  buffer_seconds: number;
}

export interface ServerInfo {
  version: string;
  tools: Record<string, boolean>;
  defaults: Config;
  grids: string[];
  resolutions: string[];
  presets: string[];
  yt_qualities: string[];
  buffer_choices: number[];
  formats: Record<string, string>;
  tutorial: { title: string; body: string }[];
  max_upload_mb: number;
  youtube_cookies: CookiesStatus;
  access_protected: boolean;
}

export interface CookiesStatus {
  configured: boolean;
  source: "env" | "upload" | null;
}

export interface SessionInfo {
  id: string;
  duration: number;
  width: number;
  height: number;
  fps: number;
  has_audio: boolean;
  verdict: boolean | null;
  plain?: boolean;
  error?: string | null;
}

export interface UploadInfo {
  id: string;
  name: string;
  size: number;
}

export interface JobInfo {
  id: string;
  mode: "encode" | "decode";
  status: "running" | "done" | "error" | "cancelled";
  text: string;
  fraction: number | null;
  log: string[];
  error: string | null;
  elapsed: number;
  describe: string;
  output_name: string;
  result: {
    width: number;
    height: number;
    block: number;
    decode_ok: boolean | null;
    warnings: string[];
    size: number;
    download: string;
  } | null;
}

async function request<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Erro ${res.status}`);
  return data as T;
}

export const api = {
  info: () => request<ServerInfo>("GET", "/api/info"),
  openSession: (source: string, password: string, config: Config, displayWidth: number, plain = false) =>
    request<SessionInfo>("POST", "/api/sessions", { source, password, config, display_width: displayWidth, plain }),
  session: (id: string) => request<SessionInfo>("GET", `/api/sessions/${id}`),
  closeSession: (id: string) => request<unknown>("DELETE", `/api/sessions/${id}`),
  streamUrl: (id: string, start: number, fmt: string) =>
    `/api/sessions/${id}/stream?start=${start.toFixed(3)}&fmt=${fmt}`,
  createJob: (mode: string, source: string, password: string, outputName: string, config: Config) =>
    request<JobInfo>("POST", "/api/jobs", { mode, source, password, output_name: outputName, config }),
  job: (id: string) => request<JobInfo>("GET", `/api/jobs/${id}`),
  cancelJob: (id: string) => request<JobInfo>("POST", `/api/jobs/${id}/cancel`),
  deleteCookies: () => request<CookiesStatus>("DELETE", "/api/youtube-cookies"),
  async uploadCookies(content: File | string): Promise<CookiesStatus> {
    const res = await fetch("/api/youtube-cookies", { method: "PUT", body: content });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `Erro ${res.status}`);
    return data as CookiesStatus;
  },
};

/** Envia um arquivo ao servidor (XHR para ter progresso). */
export function uploadFile(file: File, onProgress: (fraction: number) => void,
                           signal?: AbortSignal): Promise<UploadInfo> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("PUT", `/api/uploads?name=${encodeURIComponent(file.name)}`);
    xhr.upload.onprogress = (e) => e.lengthComputable && onProgress(e.loaded / e.total);
    xhr.onload = () => {
      let data: { error?: string } = {};
      try {
        data = JSON.parse(xhr.responseText);
      } catch {
        /* resposta sem JSON */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(data as UploadInfo);
      else reject(new Error(data.error || `Erro ${xhr.status} no envio`));
    };
    xhr.onerror = () => reject(new Error("Falha de rede no envio do arquivo."));
    xhr.onabort = () => reject(new Error("Envio cancelado."));
    signal?.addEventListener("abort", () => xhr.abort());
    xhr.send(file);
  });
}
