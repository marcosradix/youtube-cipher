// Gera o atalho HTML para TV: um arquivo para o pen-drive que abre o site já logado e toca o vídeo.
import { useState } from "react";
import type { Config } from "../api";
import { describeCipher, isYoutubeUrl, tvShortcutHtml, tvShortcutUrl } from "../settings";

interface Props {
  source: string;               // link do YouTube digitado na aba Assistir
  password: string;
  plain: boolean;
  config: Config;
  accessProtected: boolean;     // o site pede a senha de acesso (CIPHER_WEB_PASSWORD)
}

function download(name: string, content: string) {
  const url = URL.createObjectURL(new Blob([content], { type: "text/html;charset=utf-8" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function TvShortcut({ source, password, plain, config, accessProtected }: Props) {
  const [accessPassword, setAccessPassword] = useState("");
  const [withVideo, setWithVideo] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const link = source.trim();

  const generate = () => {
    setError(null);
    if (accessProtected && !accessPassword) {
      setError("Informe a senha de acesso ao site (a mesma que o navegador pediu para entrar).");
      return;
    }
    if (withVideo && !isYoutubeUrl(link)) {
      setError("Cole um link do YouTube no campo acima (arquivos enviados não servem para o atalho).");
      return;
    }
    if (withVideo && !plain && !password) {
      setError("Informe a senha secreta do vídeo no campo acima.");
      return;
    }
    const play = withVideo ? {
      source: link, password: plain ? "" : password, plain,
      config: { grid: config.grid, invert_colors: config.invert_colors, scramble_audio: config.scramble_audio },
    } : null;
    const url = tvShortcutUrl(window.location.origin, accessProtected ? accessPassword : "", play);
    download("assistir-na-tv.html", tvShortcutHtml(url, "Assistir na TV — YouTube Cipher Studio"));
  };

  return (
    <details className="card tv-shortcut">
      <summary>📺 Atalho para TV (pen-drive)</summary>
      <p className="muted">
        Gera um arquivo <b>assistir-na-tv.html</b>. Copie para um pen-drive e abra no navegador da TV: ele entra
        no site e já começa {withVideo ? "o vídeo dos campos acima" : "na aba Assistir"}. Se a TV bloquear o som
        automático, aperte OK (ou ▶) no controle.
      </p>
      <div className="form-grid">
        {accessProtected && <>
          <label htmlFor="tv-access">Senha de acesso ao site:</label>
          <input id="tv-access" type="password" value={accessPassword} autoComplete="off"
                 placeholder="A senha que o navegador pede para abrir o site..."
                 onChange={(e) => setAccessPassword(e.target.value)} />
        </>}
        <label>Conteúdo:</label>
        <div className="row radios">
          <label><input type="checkbox" checked={withVideo} onChange={(e) => setWithVideo(e.target.checked)} />{" "}
            Incluir o vídeo {plain ? "(vídeo comum)" : "e a senha secreta"} dos campos acima</label>
        </div>
        <div className="summary muted">
          {withVideo && (link || "(sem link)")}{withVideo && !plain && ` · ${describeCipher(config)}`}
        </div>
        <label />
        <div className="row"><button className="btn green" onClick={generate}>⬇ Baixar atalho HTML</button></div>
      </div>
      {error && <div className="alert error" role="alert">{error}</div>}
      <p className="muted">
        ⚠ As senhas ficam gravadas no arquivo, em texto legível: quem pegar o pen-drive consegue entrar no site e
        assistir. Guarde-o como guardaria a senha. No caminho, elas vão só no fragmento (#) do endereço, que o
        navegador não envia ao servidor nem aparece nos logs do Railway. Para invalidar atalhos antigos, troque a
        senha de acesso (CIPHER_WEB_PASSWORD).
      </p>
    </details>
  );
}
