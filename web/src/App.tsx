import { useEffect, useMemo, useState } from "react";
import { api } from "./api";
import type { Config, ServerInfo } from "./api";
import { pickFormat } from "./player/engine";
import { loadConfig, saveConfig } from "./settings";
import ConvertTab from "./tabs/ConvertTab";
import SettingsTab from "./tabs/SettingsTab";
import WatchTab from "./tabs/WatchTab";

const TABS = [
  ["watch", "▶ Assistir"],
  ["convert", "⚙️ Converter"],
  ["settings", "🛠 Configurações"],
  ["tutorial", "📖 Tutorial & Guia Técnico"],
] as const;
type TabKey = (typeof TABS)[number][0];

const TOOL_HINTS: Record<string, string> = {
  ffmpeg: "FFmpeg (apt install ffmpeg · brew install ffmpeg · winget install ffmpeg)",
  ffprobe: "ffprobe (acompanha o FFmpeg)",
  "yt-dlp": "yt-dlp (pip install -U yt-dlp) — necessário só para links do YouTube",
};

export default function App() {
  const [info, setInfo] = useState<ServerInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [config, setConfig] = useState<Config | null>(null);
  const [tab, setTab] = useState<TabKey>("watch");

  useEffect(() => {
    api.info()
      .then((data) => {
        setInfo(data);
        setConfig(loadConfig(data.defaults));
        document.title = `YouTube Cipher Studio ${data.version}`;
      })
      .catch((err) => setLoadError((err as Error).message));
  }, []);

  const format = useMemo(() => {
    if (!info) return null;
    const name = pickFormat(info.formats);
    return name ? { name, mime: info.formats[name] } : null;
  }, [info]);

  const updateConfig = (changes: Partial<Config>) => {
    setConfig((current) => {
      const next = { ...current!, ...changes };
      saveConfig(next);
      return next;
    });
  };

  if (loadError) {
    return <div className="app"><div className="alert error">Não foi possível falar com o servidor: {loadError}</div></div>;
  }
  if (!info || !config) return <div className="app loading muted">Carregando…</div>;

  const missing = Object.entries(info.tools).filter(([, ok]) => !ok).map(([name]) => name);

  return (
    <div className="app">
      <header>
        <h1>🔒 YouTube Cipher Studio <span className="muted">— Codificador, Decodificador & Player ao Vivo</span></h1>
      </header>
      {missing.length > 0 && (
        <div className="alert warning">
          ⚠ Dependências ausentes no servidor:
          <ul>{missing.map((m) => <li key={m}>{TOOL_HINTS[m] ?? m}</li>)}</ul>
        </div>
      )}
      <nav className="tabs" role="tablist">
        {TABS.map(([key, label]) => (
          <button key={key} role="tab" aria-selected={tab === key} className={tab === key ? "active" : ""}
                  onClick={() => setTab(key)}>{label}</button>
        ))}
      </nav>
      <main>
        {/* As abas ficam montadas: trocar de aba não interrompe o vídeo nem a conversão. */}
        <div hidden={tab !== "watch"}>
          <WatchTab config={config} updateConfig={updateConfig} format={format} active={tab === "watch"}
                    goToSettings={() => setTab("settings")} />
        </div>
        <div hidden={tab !== "convert"}><ConvertTab config={config} /></div>
        <div hidden={tab !== "settings"}><SettingsTab info={info} config={config} updateConfig={updateConfig} /></div>
        <div hidden={tab !== "tutorial"}>
          <div className="tab tutorial">
            {info.tutorial.map((section) => (
              <section key={section.title} className="card">
                <h3>{section.title}</h3>
                <p>{section.body}</p>
              </section>
            ))}
          </div>
        </div>
      </main>
      <footer className="muted">
        Versão web {info.version} · ofuscação, não criptografia forte · envie só vídeos que você tem direito de publicar
      </footer>
    </div>
  );
}
