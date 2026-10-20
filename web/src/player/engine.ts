// Motor do player: recebe o vídeo já decodificado pelo servidor e o entrega ao <video> por
// Media Source Extensions.
//
// Por que MSE e não <video src>: um fluxo gerado ao vivo não tem tamanho nem índice, então o
// navegador não deixaria saltar dentro do trecho já carregado. Com MSE o buffer é nosso:
// - saltos dentro do trecho carregado são instantâneos (só muda currentTime);
// - fora dele, abre-se um fluxo novo a partir do instante pedido (timestampOffset);
// - "Carregar à frente": paramos de ler a resposta quando há buffer suficiente; os pipes do
//   servidor enchem e toda a cadeia (inclusive a leitura do YouTube) espera — também pausado.
import { api } from "../api";
import type { SessionInfo } from "../api";

export interface PlayerSnapshot {
  position: number;
  duration: number;
  buffered: number;
  paused: boolean;
  buffering: boolean;
  ended: boolean;
  started: boolean;
}

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));
const START_BUFFER = 3;     // segundos carregados para começar a tocar (o áudio chega em blocos de 5 s)
const REBUFFER = 3;         // segundos carregados para retomar após o buffer esvaziar

/** Primeiro formato que o MediaSource deste navegador aceita. */
export function pickFormat(formats: Record<string, string>): string | null {
  if (typeof MediaSource === "undefined") return null;
  for (const [name, mime] of Object.entries(formats)) {
    if (MediaSource.isTypeSupported(mime)) return name;
  }
  return null;
}

export class StreamEngine {
  readonly video: HTMLVideoElement;
  private session: SessionInfo;
  private fmt: string;
  private mime: string;
  private scramble: boolean;
  bufferSeconds: number;
  onError: (message: string) => void = () => {};

  private ms = new MediaSource();
  private sb: SourceBuffer | null = null;
  private ready: Promise<void>;
  private gen = 0;
  private abort: AbortController | null = null;
  private userPaused = false;
  private stalled = true;     // buffer vazio: áudio e vídeo param juntos até ter folga de novo
  private ticker: number;
  private started = false;
  private reopenMarks: number[] = [];
  private objectUrl: string;
  private disposed = false;

  constructor(video: HTMLVideoElement, session: SessionInfo, fmt: string, mime: string,
              scramble: boolean, bufferSeconds: number) {
    this.video = video;
    this.session = session;
    this.fmt = fmt;
    this.mime = mime;
    this.scramble = scramble;
    this.bufferSeconds = bufferSeconds;
    this.ready = new Promise((resolve) => {
      this.ms.addEventListener("sourceopen", () => {
        if (!this.sb) {
          this.sb = this.ms.addSourceBuffer(this.mime);
          this.sb.mode = "segments";
          if (session.duration > 0) this.ms.duration = session.duration;
        }
        resolve();
      }, { once: true });
    });
    this.objectUrl = URL.createObjectURL(this.ms);
    video.addEventListener("error", this.onVideoError);
    this.ticker = window.setInterval(() => this.checkBuffer(), 100);
    video.src = this.objectUrl;
  }

  private onVideoError = () => {
    if (this.disposed || !this.video.error) return;
    this.gen++;
    this.abort?.abort();
    this.onError(`O navegador não conseguiu reproduzir o vídeo: ${this.video.error.message || this.video.error.code}`);
  };

  dispose(): void {
    this.disposed = true;
    this.gen++;
    this.abort?.abort();
    this.video.removeEventListener("error", this.onVideoError);
    clearInterval(this.ticker);
    this.video.pause();
    this.video.removeAttribute("src");
    this.video.load();
    URL.revokeObjectURL(this.objectUrl);
  }

  // ---------------- controle ----------------

  start(): void {
    void this.open(0, false);
  }

  get duration(): number {
    return this.session.duration;
  }

  /** Fim do trecho carregado que contém `t` (ou `t` se não houver). */
  private bufferedEnd(t: number): number {
    const ranges = this.video.buffered;
    for (let i = 0; i < ranges.length; i++) {
      if (ranges.start(i) - 0.3 <= t && t <= ranges.end(i)) return ranges.end(i);
    }
    return t;
  }

  private clamp(t: number): number {
    const d = this.session.duration;
    return Math.max(0, d > 0 ? Math.min(t, Math.max(0, d - 0.5)) : t);
  }

  seek(seconds: number): void {
    const target = this.clamp(seconds);
    const end = this.bufferedEnd(target);
    const finished = this.ms.readyState === "ended";
    if (end > target && (end - target > 0.5 || finished)) {
      this.video.currentTime = target;       // dentro do trecho carregado: instantâneo
      return;
    }
    void this.open(target, false);
  }

  seekRelative(delta: number): void {
    this.seek(this.video.currentTime + delta);
  }

  togglePause(): void {
    if (this.video.ended) {
      this.userPaused = false;
      this.seek(0);
    } else {
      this.userPaused = !this.userPaused;
    }
    this.applyHold();
  }

  set volume(value: number) {
    this.video.volume = Math.min(1, Math.max(0, value));
  }

  snapshot(): PlayerSnapshot {
    const v = this.video;
    const position = v.currentTime;
    const ended = v.ended;
    const waiting = v.readyState < HTMLMediaElement.HAVE_FUTURE_DATA || v.seeking;
    return {
      position,
      duration: this.session.duration,
      buffered: this.bufferedEnd(position),
      paused: this.userPaused,
      buffering: (this.stalled || waiting) && !this.userPaused && !ended,
      ended,
      started: this.started,
    };
  }

  droppedFrames(): number {
    return this.video.getVideoPlaybackQuality?.().droppedVideoFrames ?? 0;
  }

  /** Toca só se o usuário não pausou e há buffer suficiente. */
  private applyHold(): void {
    const v = this.video;
    if (!this.userPaused && !this.stalled) {
      if (v.paused && !v.ended) {
        v.play().catch((err: DOMException) => {
          // Sem interação recente o navegador pode bloquear o som: fica pausado até o usuário clicar.
          if (err.name === "NotAllowedError") this.userPaused = true;
        });
      }
    } else if (!v.paused) {
      v.pause();
    }
  }

  /** Para tudo quando o buffer esvazia; retoma só com folga suficiente (como o player desktop). */
  private checkBuffer(): void {
    const t = this.video.currentTime;
    const ahead = this.bufferedEnd(t) - t;
    const finished = this.ms.readyState === "ended";
    if (this.stalled) {
      if (finished || ahead >= (this.started ? REBUFFER : START_BUFFER)) {
        this.stalled = false;
        this.applyHold();
      }
    } else if (!finished && ahead <= 0.05 && !this.video.ended) {
      this.stalled = true;
      this.applyHold();
    }
  }

  // ---------------- fluxo ----------------

  private async waitIdle(): Promise<void> {
    const sb = this.sb!;
    while (sb.updating) {
      await new Promise((r) => sb.addEventListener("updateend", r, { once: true }));
    }
  }

  private async append(data: Uint8Array, gen: number): Promise<boolean> {
    const sb = this.sb!;
    for (;;) {
      if (gen !== this.gen) return false;
      await this.waitIdle();
      if (gen !== this.gen) return false;
      try {
        sb.appendBuffer(data);
        await this.waitIdle();
        return true;
      } catch (err) {
        if ((err as DOMException).name !== "QuotaExceededError") throw err;
        // Buffer do navegador cheio: libera o que já passou e espera a reprodução avançar.
        const behind = this.video.currentTime - 10;
        if (behind > 0 && sb.buffered.length && sb.buffered.start(0) < behind) {
          sb.remove(0, behind);
          await this.waitIdle();
        } else {
          await sleep(500);
        }
      }
    }
  }

  /** Abre um fluxo a partir de `start`. `keep` = continuação (reconexão), sem limpar o buffer. */
  private async open(start: number, keep: boolean): Promise<void> {
    const gen = ++this.gen;
    this.abort?.abort();
    const abort = new AbortController();
    this.abort = abort;
    if (!keep) this.started = false;
    await this.ready;
    if (gen !== this.gen || this.disposed) return;
    const sb = this.sb!;
    await this.waitIdle();
    if (gen !== this.gen) return;
    try {
      if (this.ms.readyState === "open") sb.abort();       // descarta um segmento pela metade
      if (!keep && sb.buffered.length) {
        sb.remove(0, Infinity);
        await this.waitIdle();
      }
      sb.timestampOffset = start;
      // endOfStream() encurta a duração para o fim carregado; um salto além dele a restaura.
      if (this.session.duration > 0 && this.ms.duration < this.session.duration) {
        this.ms.duration = this.session.duration;
      }
    } catch (err) {
      this.onError(`Falha ao preparar o buffer: ${(err as Error).message}`);
      return;
    }
    if (!keep) {
      this.video.currentTime = start;
      this.stalled = true;
      this.applyHold();
    }

    let reachedEnd = false;
    try {
      const res = await fetch(api.streamUrl(this.session.id, start, this.fmt), { signal: abort.signal });
      if (!res.ok || !res.body) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.error || `Erro ${res.status} ao abrir o vídeo`);
      }
      const reader = res.body.getReader();
      for (;;) {
        // Carregar à frente: só lê mais quando há espaço no buffer configurado.
        while (gen === this.gen && this.bufferedEnd(this.video.currentTime) - this.video.currentTime > this.bufferSeconds) {
          await sleep(200);
        }
        if (gen !== this.gen) {
          void reader.cancel().catch(() => {});
          return;
        }
        const { value, done } = await reader.read();
        if (done) break;
        if (!(await this.append(value, gen))) {
          void reader.cancel().catch(() => {});
          return;
        }
        this.started = true;
      }
      reachedEnd = true;
    } catch (err) {
      if (gen !== this.gen || abort.signal.aborted) return;
      if (!this.started && !keep) {
        this.onError((err as Error).message);
        return;
      }
    }
    if (gen !== this.gen) return;

    // O fluxo terminou: fim real do vídeo ou conexão perdida no meio?
    const loadedUntil = this.bufferedEnd(this.video.currentTime);
    const duration = this.session.duration;
    // O áudio embaralhado é completado até o bloco de 5 s: a imagem pode acabar até 5 s antes.
    const slack = this.scramble ? 6 : 1.5;
    const early = duration > 0 && loadedUntil < duration - slack;
    if (early && !this.reopenMarks.some((m) => Math.abs(m - loadedUntil) < 1)) {
      this.reopenMarks.push(loadedUntil);
      void this.open(loadedUntil, true);    // reabre sozinho de onde parou
      return;
    }
    if (!reachedEnd && !this.started) return;
    await this.waitIdle();
    if (gen === this.gen && this.ms.readyState === "open") {
      try {
        this.ms.endOfStream();
      } catch {
        /* já encerrado */
      }
    }
  }
}
