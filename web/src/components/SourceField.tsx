// Campo de origem: link do YouTube digitado ou arquivo escolhido no computador (enviado ao servidor).
import { useRef, useState } from "react";
import { uploadFile } from "../api";

export interface Source {
  text: string;                 // o que aparece no campo (URL ou nome do arquivo)
  file: File | null;
  uploadId: string | null;      // preenchido quando o envio termina
}

export const EMPTY_SOURCE: Source = { text: "", file: null, uploadId: null };

const ACCEPT = ".mp4,.mkv,.mov,.avi,.webm,video/*";

/** Garante que o arquivo escolhido já está no servidor e devolve a referência para a API. */
export async function resolveSource(source: Source, setSource: (s: Source) => void,
                                    onProgress: (fraction: number) => void): Promise<string> {
  if (!source.file) return source.text.trim();
  if (source.uploadId) return `upload:${source.uploadId}`;
  const info = await uploadFile(source.file, onProgress);
  setSource({ ...source, uploadId: info.id });
  return `upload:${info.id}`;
}

interface Props {
  value: Source;
  onChange: (s: Source) => void;
  placeholder: string;
  buttonText: string;
  allowUrl?: boolean;
  disabled?: boolean;
}

export default function SourceField({ value, onChange, placeholder, buttonText, allowUrl = true, disabled }: Props) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

  const pick = (file: File | undefined) => {
    if (file) onChange({ text: file.name, file, uploadId: null });
  };

  return (
    <div className={`source-field${dragging ? " dragging" : ""}`}
         onDragOver={(e) => { e.preventDefault(); setDragging(true); }}
         onDragLeave={() => setDragging(false)}
         onDrop={(e) => { e.preventDefault(); setDragging(false); pick(e.dataTransfer.files[0]); }}>
      <input
        type="text"
        value={value.file ? `📁 ${value.text}` : value.text}
        placeholder={placeholder}
        disabled={disabled}
        readOnly={!allowUrl && !value.file}
        onChange={(e) => {
          // Digitar no campo troca o arquivo escolhido por um link.
          if (!allowUrl) return;
          onChange({ text: e.target.value.replace(/^📁 /, ""), file: null, uploadId: null });
        }}
        onFocus={(e) => value.file && allowUrl && e.target.select()}
      />
      <button type="button" disabled={disabled} onClick={() => inputRef.current?.click()}>{buttonText}</button>
      <input ref={inputRef} type="file" accept={ACCEPT} hidden
             onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
    </div>
  );
}
