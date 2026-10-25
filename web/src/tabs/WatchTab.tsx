// Aba ▶ Assistir: decodifica vídeo e áudio ao vivo (no servidor) e toca no navegador.
// No modo "vídeo comum" toca um link qualquer do YouTube como está, sem senha.
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Config, SessionInfo } from "../api";
import Player from "../components/Player";
import SourceField, { EMPTY_SOURCE, resolveSource } from "../components/SourceField";
import type { Source } from "../components/SourceField";
import TvShortcut from "../components/TvShortcut";
import type { PlayerSnapshot } from "../player/engine";
import { describeCipher, isYoutubeUrl, takeTvPlay } from "../settings";

interface Props {
  config: Config;
  updateConfig: (changes: Partial<Config>) => void;
  format: { name: string; mime: string } | null;
  active: boolean;
  goToSettings: () => void;
  accessProtected: boolean;
}

export default function WatchTab({ config, updateConfig, format, active, goToSettings, accessProtected }: Props) {
  const [source, setSource] = useState<Source>(EMPTY_SOURCE);
  const [password, setPassword] = useState("");
  const [plain, setPlain] = useState(false);
  const [session, setSession] = useState<SessionInfo | null>(null);
  const [opening, setOpening] = useState(false);
  const [status, setStatus] = useState("pronto para reproduzir");
  const [error, setError] = useState<string | null>(null);
  const verdictRef = useRef<boolean | null>(null);
  const lastStatus = useRef(0);
  const [autoStart, setAutoStart] = useState(false);

  // Aberto pelo atalho para TV (página /tv): preenche os campos e começa sozinho.
  useEffect(() => {
    const tv = takeTvPlay();
    if (!tv) return;
    setPlain(tv.plain);
    setSource({ text: tv.source, file: null, uploadId: null });
    setPassword(tv.password);
    if (Object.keys(tv.config).length) updateConfig(tv.config);
    setAutoStart(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Veredito da decodificação (✓/⚠) e erros do servidor, consultados enquanto o vídeo está aberto.
  useEffect(() => {
    if (!session) return;
    verdictRef.current = null;
    const id = setInterval(async () => {
      try {
        const info = await api.session(session.id);
        verdictRef.current = info.verdict;
        if (info.error) setError(`O servidor não conseguiu ler o vídeo:\n${info.error}`);
      } catch {
        /* rede instável: tenta de novo */
      }
    }, 2000);
    return () => clearInterval(id);
  }, [session]);

  // Fecha a sessão no servidor ao trocar de vídeo, parar ou sair da página.
  useEffect(() => {
    if (!session) return;
    const close = () => navigator.sendBeacon?.(`/api/sessions/${session.id}/close`) ?? undefined;
    window.addEventListener("pagehide", close);
    return () => {
      window.removeEventListener("pagehide", close);
      void api.closeSession(session.id).catch(() => {});
    };
  }, [session]);

  const start = async () => {
    if (opening || session) return;
    setError(null);
    if (!source.text.trim() || (!plain && !password)) {
      setError(plain ? "Informe o link/arquivo de vídeo." : "Informe o link/arquivo de vídeo e a senha secreta.");
      return;
    }
    if (!source.file && !isYoutubeUrl(source.text.trim())) {
      setError(plain ? "Cole um link do YouTube ou use 'Procurar arquivo' para escolher um vídeo."
        : "Cole um link do YouTube ou use 'Procurar arquivo' para escolher um vídeo codificado.");
      return;
    }
    if (!format) {
      setError("Este navegador não suporta Media Source Extensions com H.264/AAC nem VP8/Opus. " +
               "Use uma versão recente do Chrome, Edge, Firefox ou Safari.");
      return;
    }
    setOpening(true);
    try {
      const ref = await resolveSource(source, setSource,
        (f) => setStatus(`enviando arquivo ao servidor: ${Math.round(f * 100)}%`));
      setStatus(plain ? (isYoutubeUrl(ref) ? "consultando o YouTube..." : "lendo o arquivo...")
        : isYoutubeUrl(ref) ? "consultando o YouTube e derivando a chave..." : "lendo o arquivo e derivando a chave...");
      // Como no desktop: decodifica na resolução da tela, então entrar em tela cheia não reabre o vídeo.
      const displayWidth = Math.round(window.screen.width * Math.min(2, window.devicePixelRatio || 1));
      const info = await api.openSession(ref, password, config, displayWidth, plain);
      setStatus("carregando...");
      setSession(info);
    } catch (err) {
      setStatus("pronto para reproduzir");
      setError((err as Error).message);
    } finally {
      setOpening(false);
    }
  };

  useEffect(() => {
    if (!autoStart) return;
    setAutoStart(false);
    void start();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoStart]);

  const stop = () => {
    setSession(null);
    setStatus("reprodução finalizada");
  };

  const onStatus = (s: PlayerSnapshot, dropped: number) => {
    const now = performance.now();
    if (!session || now - lastStatus.current < 1000) return;
    lastStatus.current = now;
    const verdict = session.plain ? " · vídeo comum (sem cifra)" : verdictRef.current === true ? " · ✓ decodificação verificada"
      : verdictRef.current === false ? " · ⚠ imagem ainda embaralhada: confira senha, grade e inversão de cor" : "";
    const ahead = Math.max(0, s.buffered - s.position);
    setStatus(`${session.width}x${session.height} @ ${session.fps.toFixed(2)} fps · buffer ${ahead.toFixed(0)}s · ` +
              `descartados: ${dropped}${verdict}`);
  };

  return (
    <div className="tab watch-tab">
      <div className="card form-grid">
        <label>Tipo de vídeo:</label>
        <div className="row radios" role="radiogroup">
          <label><input type="radio" name="watch-kind" checked={!plain} disabled={!!session || opening}
                        onChange={() => setPlain(false)} /> 🔒 Cifrado (com senha)</label>
          <label><input type="radio" name="watch-kind" checked={plain} disabled={!!session || opening}
                        onChange={() => setPlain(true)} /> ▶ Vídeo comum (não cifrado)</label>
        </div>
        <label>Link do YouTube ou arquivo:</label>
        <SourceField value={source} onChange={setSource} disabled={!!session || opening}
                     placeholder={plain ? "Cole a URL de qualquer vídeo do YouTube..."
                       : "Cole a URL do YouTube ou escolha um vídeo codificado..."}
                     buttonText="Procurar arquivo" />
        <label htmlFor="watch-password">{plain ? "" : "Senha secreta:"}</label>
        <div className="row">
          {!plain && (
            <input id="watch-password" type="password" value={password} placeholder="A mesma senha usada na codificação..."
                   autoComplete="current-password" onChange={(e) => setPassword(e.target.value)}
                   onKeyDown={(e) => e.key === "Enter" && start()} />
          )}
          <button className="btn green" disabled={!!session || opening} onClick={start}>
            {opening ? "Abrindo…" : "▶ Assistir"}
          </button>
          <button className="btn red" disabled={!session} onClick={stop}>⏹ Parar</button>
        </div>
        <div className="summary muted">
          {plain ? "O vídeo é tocado como está no YouTube, sem senha e sem desembaralhar." : (
            <>Parâmetros: {describeCipher(config)}{" "}
              (<button className="link" onClick={goToSettings}>altere em 🛠 Configurações</button>)</>
          )}
        </div>
      </div>

      <TvShortcut source={source.file ? "" : source.text} password={password} plain={plain} config={config}
                  accessProtected={accessProtected} />

      {error && <div className="alert error" role="alert">{error}<button className="close" onClick={() => setError(null)}>×</button></div>}

      <Player
        session={session}
        format={format}
        scramble={!session?.plain && config.scramble_audio}
        bufferSeconds={config.buffer_seconds}
        timelineStyle={config.timeline_style}
        volume={config.volume}
        shortcutsActive={active}
        onStyleChange={(timeline_style) => updateConfig({ timeline_style })}
        onVolumeChange={(volume) => updateConfig({ volume })}
        onError={(message) => {
          setError(message);
          setSession(null);
          setStatus(`erro — ${message.split("\n")[0]}`);
        }}
        onStatus={onStatus}
      />
      <div className="status muted">Status: {status}</div>
    </div>
  );
}
