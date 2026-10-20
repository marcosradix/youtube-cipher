// Aba 🛠 Configurações: as mesmas opções do desktop, salvas neste navegador.
import type { ReactNode } from "react";
import type { Config, ServerInfo } from "../api";
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

      <div className="row center">
        <span className="muted">As configurações são salvas automaticamente neste navegador.</span>
        <button className="btn gray" onClick={() => updateConfig({ ...info.defaults, volume: config.volume })}>
          Restaurar padrões
        </button>
      </div>
    </div>
  );
}
