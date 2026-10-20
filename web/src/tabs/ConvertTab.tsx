// Aba ⚙️ Converter: codifica (para publicar no YouTube) ou decodifica um vídeo inteiro em MP4.
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Config, JobInfo } from "../api";
import SourceField, { EMPTY_SOURCE, resolveSource } from "../components/SourceField";
import type { Source } from "../components/SourceField";
import { describeCipher, isYoutubeUrl } from "../settings";

function formatEta(seconds: number): string {
  const s = Math.floor(Math.max(0, seconds));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function formatSize(bytes: number): string {
  return bytes >= 1 << 30 ? `${(bytes / (1 << 30)).toFixed(2)} GB` : `${(bytes / (1 << 20)).toFixed(1)} MB`;
}

function suggestName(source: Source, mode: string): string {
  const suffix = mode === "encode" ? "_encoded.mp4" : "_decoded.mp4";
  if (source.file) return source.file.name.replace(/\.[^.]+$/, "").replace(/_(encoded|decoded)$/, "") + suffix;
  return `video${suffix}`;
}

export default function ConvertTab({ config }: { config: Config }) {
  const [mode, setMode] = useState<"encode" | "decode">("encode");
  const [source, setSource] = useState<Source>(EMPTY_SOURCE);
  const [outputName, setOutputName] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [showPassword, setShowPassword] = useState(false);
  const [job, setJob] = useState<JobInfo | null>(null);
  const [uploading, setUploading] = useState<number | null>(null);
  const [message, setMessage] = useState<{ kind: "error" | "warning" | "success"; text: string } | null>(null);
  const [localLog, setLocalLog] = useState<string[]>([]);
  const logRef = useRef<HTMLPreElement>(null);
  const running = uploading !== null || job?.status === "running";

  const changeSource = (s: Source) => {
    setSource(s);
    if (s.file || isYoutubeUrl(s.text)) setOutputName(suggestName(s, mode));
  };

  const changeMode = (m: "encode" | "decode") => {
    setMode(m);
    if (m === "encode" && !source.file) setSource(EMPTY_SOURCE);
    if (source.file || source.text) setOutputName(suggestName(source, m));
  };

  // Acompanha a tarefa no servidor.
  useEffect(() => {
    if (!job || job.status !== "running") return;
    const id = setInterval(async () => {
      try {
        const next = await api.job(job.id);
        setJob(next);
        if (next.status !== "running") finish(next);
      } catch {
        /* rede instável: tenta de novo */
      }
    }, 500);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job?.id, job?.status]);

  useEffect(() => {
    logRef.current?.scrollTo(0, logRef.current.scrollHeight);
  }, [job?.log.length, localLog.length]);

  const finish = (done: JobInfo) => {
    if (done.status === "done" && done.result) {
      const r = done.result;
      let text = `Vídeo pronto: ${done.output_name} (${formatSize(r.size)})\nResolução: ${r.width}x${r.height}`;
      if (done.mode === "encode") text += `\n\nPara decodificar, use a mesma senha e:\n${done.describe}`;
      if (r.warnings.length) setMessage({ kind: "warning", text: `${text}\n\n⚠ ${r.warnings.join("\n")}` });
      else setMessage({ kind: "success", text });
    } else if (done.status === "error") {
      setMessage({ kind: "error", text: done.error || "Erro no processamento." });
    }
  };

  const start = async () => {
    if (running) return;
    setMessage(null);
    if (!source.text.trim() || !outputName.trim() || !password) {
      setMessage({ kind: "warning", text: "Preencha o vídeo de origem, o nome do arquivo e a senha." });
      return;
    }
    if (mode === "encode" && password !== confirm) {
      setMessage({ kind: "warning", text: "A confirmação não confere com a senha." });
      return;
    }
    if (!source.file && !isYoutubeUrl(source.text.trim())) {
      setMessage({ kind: "warning", text: "Escolha um arquivo de vídeo ou cole um link do YouTube." });
      return;
    }
    setJob(null);
    setLocalLog([]);
    try {
      setUploading(0);
      const ref = await resolveSource(source, setSource, (f) => setUploading(f));
      setUploading(null);
      setJob(await api.createJob(mode, ref, password, outputName.trim(), config));
    } catch (err) {
      setUploading(null);
      setLocalLog((l) => [...l, `Erro: ${(err as Error).message}`]);
      setMessage({ kind: "error", text: (err as Error).message });
    }
  };

  const cancel = async () => {
    if (job?.status === "running") setJob(await api.cancelJob(job.id));
  };

  let statusText = "Aguardando início...";
  let fraction = 0;
  if (uploading !== null) {
    statusText = `Enviando arquivo ao servidor: ${Math.round(uploading * 100)}%`;
    fraction = uploading;
  } else if (job) {
    fraction = job.fraction ?? 0;
    statusText = job.text;
    if (job.status === "running" && fraction > 0.03 && fraction < 1) {
      statusText += `  ·  restante ~${formatEta(job.elapsed / fraction - job.elapsed)}`;
    } else if (job.status === "done") {
      statusText = `✅ Concluído em ${formatEta(job.elapsed)} — ${job.output_name}`;
    }
  }
  const extra = mode === "encode" ? ` · saída ${config.resolution}` : "";

  return (
    <div className="tab convert-tab">
      <div className="card form-grid">
        <label className="bold">Operação:</label>
        <div className="row radios">
          <label><input type="radio" checked={mode === "encode"} disabled={running}
                        onChange={() => changeMode("encode")} /> 🔒 Codificar (preparar para o YouTube)</label>
          <label><input type="radio" checked={mode === "decode"} disabled={running}
                        onChange={() => changeMode("decode")} /> 🔓 Decodificar (arquivo ou URL → MP4)</label>
        </div>

        <label>Vídeo de origem:</label>
        <SourceField value={source} onChange={changeSource} disabled={running} allowUrl={mode === "decode"}
                     placeholder={mode === "encode" ? "Selecione o vídeo no computador (ou arraste-o para cá)..."
                       : "Selecione o vídeo codificado ou cole a URL do YouTube..."}
                     buttonText="Selecionar vídeo..." />

        <label htmlFor="conv-output">Salvar como (.mp4):</label>
        <input id="conv-output" type="text" value={outputName} disabled={running}
               placeholder="Nome do arquivo processado (o download fica disponível ao final)..."
               onChange={(e) => setOutputName(e.target.value)} />

        <label htmlFor="conv-password">Senha secreta:</label>
        <div className="row">
          <input id="conv-password" type={showPassword ? "text" : "password"} value={password} disabled={running}
                 placeholder="Digite a senha secreta..." autoComplete="new-password"
                 onChange={(e) => setPassword(e.target.value)} />
          <label className="check"><input type="checkbox" checked={showPassword}
                                          onChange={(e) => setShowPassword(e.target.checked)} /> Mostrar senha</label>
        </div>

        {mode === "encode" && (
          <>
            <label htmlFor="conv-confirm">Confirmar senha:</label>
            <input id="conv-confirm" type={showPassword ? "text" : "password"} value={confirm} disabled={running}
                   placeholder="Repita a senha — sem ela o vídeo não pode ser recuperado"
                   autoComplete="new-password" onChange={(e) => setConfirm(e.target.value)} />
          </>
        )}
        <div className="summary muted">Parâmetros: {describeCipher(config)}{extra}</div>
      </div>

      <div className="card">
        <div className="status-text">{statusText}</div>
        <div className="progress"><div style={{ width: `${Math.round(fraction * 100)}%` }} /></div>
        <div className="row actions">
          <button className="btn primary big" disabled={running} onClick={start}>🚀 Iniciar processamento</button>
          <button className="btn red big" disabled={job?.status !== "running"} onClick={cancel}>Cancelar</button>
          {job?.status === "done" && job.result && (
            <a className="btn green big" href={job.result.download} download={job.output_name}>⬇ Baixar {job.output_name}</a>
          )}
        </div>
      </div>

      {message && (
        <div className={`alert ${message.kind}`} role="alert">
          {message.text}
          <button className="close" onClick={() => setMessage(null)}>×</button>
        </div>
      )}

      <pre className="log" ref={logRef}>{[...(job?.log ?? []), ...localLog].join("\n")}</pre>
    </div>
  );
}
