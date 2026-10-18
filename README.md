# YouTube Cipher Studio 2.3

Embaralha vídeo e áudio para publicação no YouTube e os restaura — em arquivo MP4 ou
ao vivo, direto do link, sem gravar nada no disco.

## Uso responsável

Este projeto existe para compartilhar **o seu próprio conteúdo** de forma reservada: quem
encontra o vídeo por acaso vê só um mosaico, e quem tem a senha assiste normalmente.

- **Envie apenas vídeos que você tem direito de publicar.** Embaralhar um vídeo não muda quem
  detém os direitos dele nem as regras da plataforma.
- **As regras do YouTube continuam valendo**, incluindo as políticas de spam e práticas
  enganosas e os Termos de Serviço. O YouTube pode remover vídeos ou restringir canais a
  qualquer momento; prefira publicar como "Não listado" e testar num canal separado.
- **O player usa o [yt-dlp](https://github.com/yt-dlp/yt-dlp)** para ler o vídeo do YouTube.
  Verifique se esse uso é permitido no seu caso.
- **É ofuscação, não criptografia forte.** Não use para conteúdo cuja exposição traria dano
  real; para isso, use um arquivo criptografado de verdade (veja a seção 6 do tutorial no app).
- O software é fornecido "como está", sem garantias (veja a [licença](LICENSE)).

## Instalação

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# FFmpeg no PATH: brew install ffmpeg | winget install ffmpeg | apt install ffmpeg
```

## Uso

```bash
python youtube_cipher_gui.py                                   # interface gráfica
python -m cipher_studio encode video.mp4 video_encoded.mp4     # CLI (senha pedida no terminal
python -m cipher_studio decode "https://youtu.be/..." out.mp4  #      ou via CIPHER_PASSWORD)
```

Para decodificar, use a mesma senha **e** os mesmos parâmetros da cifra
(grade, inversão de cor, embaralhamento de áudio).

## Como testar com o seu vídeo

Abra o programa com o ambiente virtual ativo (ele já inclui o `yt-dlp`):

```bash
source .venv/bin/activate
python youtube_cipher_gui.py
```

### 1. Teste local (sem YouTube)

**Codificar**
1. Aba **⚙️ Converter** → marque **🔒 Codificar**.
2. **Selecionar vídeo...** → escolha o vídeo (a saída `_encoded.mp4` é sugerida automaticamente).
3. Digite a senha e a confirmação → **🚀 Iniciar processamento**.

Abra o `_encoded.mp4` em qualquer player: deve aparecer um mosaico em negativo, com som irreconhecível.

**Assistir decodificado**
1. Aba **▶ Assistir** → **Procurar arquivo** → escolha o `_encoded.mp4`.
2. Digite a senha → **▶ Assistir**.
3. Após alguns segundos o status mostra **✓ decodificação verificada**.
   Com uma senha errada, aparece **⚠ imagem ainda embaralhada**.

**Salvar decodificado (opcional):** aba **⚙️ Converter** → **🔓 Decodificar** → escolha o
`_encoded.mp4`, digite a senha e inicie. O resultado é um `_decoded.mp4`.

### 2. Teste pelo YouTube

1. Envie o `_encoded.mp4` ao YouTube como **Não listado**.
2. Aguarde o processamento em HD (720p/1080p disponível no player do YouTube).
3. Aba **▶ Assistir** → cole o link → senha → **▶ Assistir**.

### Dicas

- Comece com um vídeo curto (30 s a 1 min): codificar em 1080p com preset `medium`
  leva mais ou menos o tempo do próprio vídeo.
- Para testar mais rápido: **🛠 Configurações** → resolução **720p** e velocidade **veryfast**.
- Grade, inversão de cor e áudio precisam ser iguais ao codificar e ao decodificar.
- O mesmo teste pela linha de comando (a senha é pedida no terminal):

  ```bash
  python -m cipher_studio encode ~/Movies/meu_video.mp4 ~/Movies/teste_encoded.mp4
  python -m cipher_studio decode ~/Movies/teste_encoded.mp4 ~/Movies/teste_decoded.mp4
  ```

- Se algo der errado, o registro detalhado fica na parte de baixo da aba **⚙️ Converter**.

## Player

A aba **▶ Assistir** decodifica vídeo e áudio em memória e oferece:

- **Indicador de carregamento** no centro da tela ao abrir o vídeo, após um salto ou quando a rede engasga.
- **Tempo atual / duração total** e **linha do tempo clicável e arrastável**, com balão mostrando o
  tempo sob o cursor.
- **Cinco estilos arredondados** para a linha do tempo, trocados na própria barra do player
  (**Estilo**) ou em **🛠 Configurações**: Pílula, Cápsula, Segmentos, Pontos e Gradiente.
  A cor do estilo também é usada nos ícones sobre o vídeo.
- Botões **⟲10 / 10⟳**, pausa, volume e **tela cheia**. Na tela cheia aparece só o vídeo: os controles e o cursor somem após 2,5 s parado e voltam ao mexer o mouse ou clicar (o primeiro clique só traz os controles). O player já decodifica na resolução da tela, então entrar na tela cheia não interrompe o som nem deixa a imagem borrada.
- **Buffer à frente:** o player carrega e decodifica adiante da reprodução, **inclusive pausado**.
  Para assistir com fluidez numa conexão lenta, pause, espere o trecho carregado crescer
  (tom mais claro na linha do tempo; o status mostra `buffer Ns`) e dê play. O quanto carregar
  (30 s a 5 min) fica em **🛠 Configurações → Carregar à frente**. Os quadros ficam na memória
  compactados em JPEG (cerca de 2–3 MB por segundo de vídeo, no máximo 800 MB).
- **Sem engasgos:** se o buffer esvaziar, áudio e vídeo param juntos e só retomam com alguns
  segundos carregados.
- **Saltos instantâneos** dentro do trecho já carregado; fora dele, o player reabre o vídeo
  naquele ponto. Se a conexão cair no meio, ele reabre sozinho de onde parou.

| Atalho | Ação |
|---|---|
| Espaço ou clique no vídeo | Pausar / continuar |
| ← / → | Voltar / avançar 10 s |
| F ou duplo clique no vídeo | Tela cheia |
| Esc | Sair da tela cheia |

Ao saltar, o áudio recomeça no início do bloco de 5 s que contém o instante escolhido
(só blocos inteiros podem ser desembaralhados) e descarta o trecho anterior, então som e imagem
continuam sincronizados. Em links do YouTube, cada salto leva de 1 a 5 s, dependendo da rede.

## Solução de problemas

### `CERTIFICATE_VERIFY_FAILED` ao abrir um link do YouTube

Mensagem típica:

```
yt-dlp não conseguiu acessar o vídeo:
ERROR: [youtube] <id-do-vídeo>: Unable to download API page:
[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed: unable to get local issuer certificate
```

**Causa:** o Python baixado do python.org no macOS não usa os certificados do sistema,
então o `yt-dlp` não consegue validar a conexão HTTPS com o YouTube.

**Correção automática:** o programa já usa os certificados do pacote `certifi`
(veja `cipher_studio/core/media.py`). Basta garantir que as dependências estão instaladas:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

Feche e abra o programa novamente.

**Correção para o sistema inteiro (opcional):** resolve o mesmo erro em qualquer programa
Python da máquina. Execute uma vez o instalador de certificados da sua versão do Python:

```bash
open "/Applications/Python 3.X/Install Certificates.command"   # troque 3.X pela sua versão
```

### Áudio com um "fantasma" metálico do som embaralhado ao fundo

**Causa:** vídeos codificados antes da versão 2.1. A inversão espectral leva os graves da voz
para logo abaixo de 12 kHz, e as conversões 24 ↔ 48 kHz feitas pelo filtro padrão do FFmpeg
cortavam justamente essa região. O resultado era a voz sem graves e um resíduo do áudio
embaralhado por baixo. A versão 2.1 usa um conversor próprio de alta precisão
(`HalfbandResampler`, em `cipher_studio/core/audio.py`): numa simulação com fala passando por
AAC e Opus do YouTube, a qualidade recuperada subiu de ~4 dB para 31–36 dB, praticamente a
mesma do áudio sem cifra.

**Solução:** codifique o vídeo de novo e publique a nova versão. O estrago acontece na
codificação, então o decodificador novo não consegue recuperar um vídeo antigo por completo.
Vídeos antigos continuam abrindo (o método de embaralhamento é o mesmo); só o áudio deles
permanece degradado. A imagem não é afetada.

### Outros problemas comuns

| Sintoma | Solução |
|---|---|
| Outro erro do yt-dlp (formato indisponível, assinatura, etc.) | `pip install -U yt-dlp`, porque o YouTube muda com frequência |
| "⚠ imagem ainda embaralhada" | Senha, grade ou inversão de cor diferentes das usadas na codificação, ou o vídeo não foi gerado por este programa |
| "FFmpeg não encontrado" | `brew install ffmpeg` (macOS), `winget install ffmpeg` (Windows), `apt install ffmpeg` (Linux) |
| Estalos a cada ¼ de segundo e áudio "picotado" no player (versões até 2.1.0) | Atualize. O player usava a faixa AAC do YouTube, que chega ~36 ms atrasada e desalinha as fatias de 250 ms. Desde a 2.1.1 ele usa sempre a faixa Opus |
| Cliques no som (vídeos codificados antes da 2.3) | Codifique o vídeo de novo com a versão 2.3 ou mais nova e publique a nova versão. No formato antigo (v2), cada corte entre fatias de 250 ms era um transiente para o AAC/Opus, que deixava ~30 ms de ruído em volta: medido contra o áudio original de um vídeo real, eram ~18 estouros por minuto. O formato v3 coloca margens de 25 ms (o próprio som espelhado, desvanecendo até zero) em volta de cada fatia, e o corte acontece no silêncio: os estouros caíram para ~2 por minuto, no nível do ruído da medição. Vídeos v2 continuam abrindo (formato detectado automaticamente) |
| Arquivo codificado muito maior que o original | Normal: o mosaico é difícil de comprimir. Use CRF 20 (padrão desde a 2.2) ou 23 em **🛠 Configurações**. Como o YouTube recomprime tudo, CRF 16 gera um arquivo ~60% maior que o CRF 20 com diferença final imperceptível |
| Sem som no player | Verifique a saída de áudio padrão do sistema |
| Reprodução engasgando | Pause e deixe o buffer carregar; se não bastar, em **🛠 Configurações** escolha 720p ou diminua a janela |

## Estrutura

| Módulo | Função |
|---|---|
| `cipher_studio/core/keys.py` | scrypt + permutação Fisher–Yates via HMAC-SHA256 (estável entre versões) |
| `cipher_studio/core/video.py` | grade de macroblocos, transformação, suavização de emendas, verificação do decode |
| `cipher_studio/core/audio.py` | inversão espectral exata + embaralhamento de fatias de 250 ms |
| `cipher_studio/core/media.py` | FFmpeg/ffprobe/yt-dlp por pipes |
| `cipher_studio/core/pipeline.py` | encode/decode de arquivos (GUI e CLI) |
| `cipher_studio/core/player.py` | player ao vivo sincronizado pelo relógio do áudio |
| `cipher_studio/gui/` | interface CustomTkinter, componentes do player (`player_widgets.py`) e tutorial |

## Testes

```bash
pip install -r requirements-dev.txt && pytest -q
```

O teste ponta a ponta codifica um vídeo, recomprime em 480p (simulando o YouTube) e
confere que o decode recupera imagem e áudio e detecta senha errada.

> Ofuscação, não criptografia forte — veja a seção 6 do tutorial no app.

## Licença

[MIT](LICENSE). O FFmpeg não é distribuído com este projeto: ele é instalado à parte e segue
a licença dele (LGPL/GPL, conforme a compilação). As demais dependências (NumPy, Pillow,
CustomTkinter, sounddevice, yt-dlp, certifi) têm licenças permissivas compatíveis.
