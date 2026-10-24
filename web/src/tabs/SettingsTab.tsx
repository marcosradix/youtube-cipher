// Aba 🛠 Configurações: as mesmas opções do desktop, salvas neste navegador.
import { useRef, useState } from "react";
import type { ReactNode } from "react";
import { api } from "../api";
import type { Config, CookiesStatus, ServerInfo } from "../api";
import { GRID_LABELS, TIMELINE_STYLES } from "../settings";

interface Props {
  info: ServerInfo;
  config: Config;
  updateConfig: (changes: Partial<Config>) => void;
}

function Section({ title, subtitle, children }: { title: string; subtitle?: string; children: ReactNode }) {
  return (
    <section className="card settings-section">
      <h3>{title}</h3>
      {subtitle && <p className="muted">{subtitle}</p>}
      <div className="form-grid">{children}</div>
    </section>
  );
}

function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <label className="switch">
      <input type="checkbox" role="switch" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span className="track" aria-hidden="true" />
      {label}
    </label>
  );
}

function YoutubeCookies({ initial }: { initial: CookiesStatus }) {
  const [status, setStatus] = useState(initial);
  const [message, setMessage] = useState<{ ok: boolean; text: string } | null>(null);
  const [pasted, setPasted] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const upload = async (content: File | string | undefined) => {
    if (!content) return;
    try {
      setStatus(await api.uploadCookies(content));
      setMessage({ ok: true, text: "Cookies salvos. Tente abrir o link do YouTube de novo." });
      if (typeof content === "string") setPasted("");
    } catch (err) {
      setMessage({ ok: false, text: (err as Error).message });
    }
  };

  const remove = async () => {
    setStatus(await api.deleteCookies());
    setMessage(null);
  };

  return (
    <Section title="YouTube no servidor"
             subtitle={"Em servidores na nuvem o YouTube costuma pedir \"confirme que você não é um robô\". " +
                       "Envie o cookies.txt de uma conta logada (de preferência uma conta secundária) para liberar."}>
      <label>Cookies do YouTube:</label>
      <div className="row">
        <span className={status.configured ? "ok-text" : "muted"}>
          {status.configured
            ? `✓ configurados${status.source === "env" ? " (variável CIPHER_YT_COOKIES)" : ""}`
            : "não configurados"}
        </span>
        <button className="btn" onClick={() => inputRef.current?.click()}>Enviar cookies.txt</button>
        {status.configured && status.source === "upload" && (
          <button className="btn gray" onClick={remove}>Remover</button>
        )}
        <input ref={inputRef} type="file" accept=".txt,text/plain" hidden
               onChange={(e) => { void upload(e.target.files?.[0]); e.target.value = ""; }} />
      </div>
      <label htmlFor="cookies-text">Ou cole o texto:</label>
      <div className="cookies-paste">
        <textarea id="cookies-text" rows={5} spellCheck={false} autoComplete="off" value={pasted}
                  placeholder={"# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t…"}
                  onChange={(e) => setPasted(e.target.value)} />
        <button className="btn" disabled={!pasted.trim()} onClick={() => void upload(pasted)}>
          Salvar cookies colados
        </button>
      </div>
      {message && <div className={`summary ${message.ok ? "ok-text" : "error-text"}`}>{message.text}</div>}
      <div className="summary muted">
        Como obter: no computador, abra o YouTube numa janela anônima, faça login, instale a extensão
        "Get cookies.txt LOCALLY" e exporte os cookies de youtube.com (envie o arquivo ou cole o conteúdo
        dele acima; a exportação em JSON de extensões como "Cookie-Editor" ou o cabeçalho "Cookie" no formato
        nome=valor; nome=valor também servem); depois feche a janela anônima sem
        sair da conta. Enviados por aqui, valem até o servidor reiniciar; para mantê-los após novos deploys,
        coloque o conteúdo do arquivo na variável CIPHER_YT_COOKIES do Railway.
      </div>
    </Section>
  );
}

export default function SettingsTab({ info, config, updateConfig }: Props) {
  return (
    <div className="tab settings-tab">
      <Section title="Parâmetros da cifra" subtitle="Precisam ser IGUAIS na codificação e na decodificação de um vídeo.">
        <label>Grade de macroblocos:</label>
        <select value={config.grid} onChange={(e) => updateConfig({ grid: e.target.value })}>
          {info.grids.map((g) => <option key={g} value={g}>{GRID_LABELS[g] ?? g}</option>)}
        </select>
        <label>Ofuscação de cor:</label>
        <Switch checked={config.invert_colors} onChange={(invert_colors) => updateConfig({ invert_colors })}
                label="Inverter cores (efeito negativo)" />
        <label>Áudio:</label>
        <Switch checked={config.scramble_audio} onChange={(scramble_audio) => updateConfig({ scramble_audio })}
                label="Embaralhar áudio (inversão espectral + fatias)" />
      </Section>

      <Section title="Exportação (codificação)">
        <label>Resolução enviada ao YouTube:</label>
        <select value={config.resolution} onChange={(e) => updateConfig({ resolution: e.target.value })}>
          {info.resolutions.map((r) => <option key={r}>{r}</option>)}
        </select>
        <label>Qualidade H.264 (12 = máxima, 24 = leve):</label>
        <div className="row">
          <input type="range" min={12} max={24} step={1} value={config.crf}
                 onChange={(e) => updateConfig({ crf: Number(e.target.value) })} />
          <span className="crf">CRF {config.crf}</span>
        </div>
        <label>Velocidade do encoder:</label>
        <select value={config.preset} onChange={(e) => updateConfig({ preset: e.target.value })}>
          {info.presets.map((p) => <option key={p}>{p}</option>)}
        </select>
      </Section>

      <Section title="Reprodução e decodificação">
        <label>Qualidade máxima do YouTube:</label>
        <select value={config.yt_quality} onChange={(e) => updateConfig({ yt_quality: e.target.value })}>
          {info.yt_qualities.map((q) => <option key={q}>{q}</option>)}
        </select>
        <label>Pós-processamento:</label>
        <Switch checked={config.deblock} onChange={(deblock) => updateConfig({ deblock })}
                label="Suavizar emendas entre blocos" />
        <label>Estilo da linha do tempo:</label>
        <select value={config.timeline_style} onChange={(e) => updateConfig({ timeline_style: e.target.value })}>
          {Object.entries(TIMELINE_STYLES).map(([key, [label]]) => <option key={key} value={key}>{label}</option>)}
        </select>
        <label>Carregar à frente (buffer):</label>
        <select value={config.buffer_seconds} onChange={(e) => updateConfig({ buffer_seconds: Number(e.target.value) })}>
          {info.buffer_choices.map((s) => <option key={s} value={s}>{s} s</option>)}
        </select>
      </Section>

      <YoutubeCookies initial={info.youtube_cookies} />

      <div className="row center">
        <span className="muted">As configurações são salvas automaticamente neste navegador.</span>
        <button className="btn gray" onClick={() => updateConfig({ ...info.defaults, volume: config.volume })}>
          Restaurar padrões
        </button>
      </div>
    </div>
  );
}
