"""Conteúdo da aba 📖 Tutorial & Guia Técnico."""

SECTIONS = [
    (
        "1. Por que macroblocos e não 'chuvisco' de 1×1 pixel?",
        "O YouTube nunca guarda o seu MP4 original: ele recomprime tudo com perdas (VP9/AV1 no vídeo, "
        "Opus/AAC no áudio).\n\n"
        "• Embaralhar pixels individuais gera um chuvisco que o compressor destrói por completo — "
        "não há como reconstruir a imagem depois.\n"
        "• Este programa embaralha MACROBLOCOS (ex.: 60×60 px em 1080p). O interior de cada bloco "
        "sobrevive à compressão; só as emendas entre blocos sofrem um pouco, e o filtro de "
        "'suavização de emendas' corrige isso no decode."
    ),
    (
        "2. Passo a passo: codificar e subir para o YouTube",
        "1. Em '🛠 Configurações', escolha a grade (32×18 é a mais robusta) e a resolução de exportação "
        "(1080p recomendado — o YouTube dá mais bitrate para vídeos HD).\n"
        "2. Em '⚙️ Converter', marque '🔒 Codificar' e selecione o vídeo original.\n"
        "3. Escolha onde salvar (o programa sugere '_encoded.mp4').\n"
        "4. Digite e CONFIRME a senha. Sem ela o vídeo não pode ser recuperado — não existe 'esqueci a senha'.\n"
        "5. Clique em '🚀 Iniciar Processamento' e envie o arquivo gerado ao YouTube.\n"
        "6. Anote os parâmetros mostrados ao final (grade, inversão de cor, áudio): quem for "
        "decodificar precisa usar os mesmos.\n"
        "7. Após o upload, espere o YouTube terminar o processamento em HD. Qualquer resolução "
        "decodifica, mas quanto maior, mais nítido."
    ),
    (
        "3. Passo a passo: assistir ao vivo ou salvar decodificado",
        "ASSISTIR SEM BAIXAR:\n"
        "1. Em '▶ Assistir', cole o link do YouTube (ou escolha um arquivo codificado).\n"
        "2. Digite a senha e clique em '▶ Assistir'.\n"
        "3. O yt-dlp entrega os fluxos de vídeo e áudio ao FFmpeg; tudo é decodificado na memória e "
        "nada é gravado no disco. O áudio só pode ser desembaralhado em blocos completos de 5 s, por "
        "isso a reprodução começa após um breve buffer.\n"
        "4. Após alguns segundos o status mostra '✓ decodificação verificada' ou um aviso de que a "
        "imagem ainda parece embaralhada (senha ou parâmetros errados).\n\n"
        "CONTROLES DO PLAYER:\n"
        "• Clique ou arraste na linha do tempo para ir a qualquer instante; o balão mostra o tempo "
        "sob o cursor. Em links do YouTube cada salto leva de 1 a 5 s (o indicador de carregamento "
        "aparece no centro).\n"
        "• O estilo da linha do tempo (Pílula, Cápsula, Segmentos, Pontos, Gradiente) muda na barra "
        "do player ou em Configurações.\n"
        "• Atalhos: Espaço ou clique no vídeo = pausar · ←/→ = 10 s · F ou duplo clique = tela "
        "cheia · Esc = sair da tela cheia.\n"
        "• Buffer: o player carrega à frente mesmo pausado. Em conexão lenta, pause, espere o "
        "trecho mais claro da linha do tempo crescer e dê play. O tamanho do buffer fica em "
        "Configurações → Carregar à frente. Saltos dentro do trecho carregado são instantâneos.\n\n"
        "SALVAR O VÍDEO RESTAURADO EM MP4:\n"
        "1. Em '⚙️ Converter', marque '🔓 Decodificar'.\n"
        "2. Cole a URL do YouTube ou escolha o arquivo codificado, defina onde salvar, digite a senha "
        "e inicie. O download usa a qualidade escolhida em Configurações."
    ),
    (
        "4. Como funciona por dentro",
        "• derive_key(senha): scrypt (memória e CPU intensivos) gera uma chave de 256 bits. Testar "
        "senhas por força bruta fica milhares de vezes mais caro que um SHA-256 simples.\n\n"
        "• permutation(n, chave, rótulo): Fisher–Yates alimentado por HMAC-SHA256 em modo contador. "
        "Não depende do gerador aleatório do NumPy, então um vídeo codificado hoje continua "
        "decodificável em qualquer versão futura das bibliotecas. Vídeo e áudio usam rótulos "
        "diferentes, gerando permutações independentes.\n\n"
        "• Grade fixa (colunas × linhas): a cifra é definida em blocos, não em pixels. Um vídeo "
        "codificado em 1080p com grade 32×18 tem blocos de 60 px; se o YouTube entregar 720p, os "
        "blocos têm 40 px e a mesma permutação continua valendo. O FFmpeg ajusta cada quadro para o "
        "múltiplo exato da grade antes do decode.\n\n"
        "• transform_frame: inverte as cores (255 − pixel, opcional) e reordena os blocos com "
        "reshape/swapaxes do NumPy, sem laços em Python.\n\n"
        "• Áudio: inversão espectral exata (×(−1)ⁿ a 24 kHz — graves viram agudos e vice-versa, de "
        "forma perfeitamente reversível) + embaralhamento de 20 fatias de 250 ms por bloco de 5 s. "
        "O último bloco é completado com silêncio para nada do final ser perdido.\n\n"
        "• Pipeline: os quadros vão do FFmpeg para o NumPy e de volta ao FFmpeg por pipe, com uma "
        "única compressão H.264 no final (sem arquivo intermediário com perdas)."
    ),
    (
        "5. Configurações explicadas",
        "PARÂMETROS DA CIFRA (precisam ser iguais no encode e no decode):\n"
        "• Grade: 32×18 (mais robusta), 64×36 (equilibrada) ou 80×45 (mosaico fino, para 1440p/4K). "
        "Vídeos verticais usam a grade girada automaticamente.\n"
        "• Inversão de cor: efeito negativo adicional.\n"
        "• Embaralhar áudio: desligado, o áudio segue intacto.\n\n"
        "EXPORTAÇÃO:\n"
        "• Resolução: tamanho do arquivo enviado ao YouTube. 'Auto' mantém a resolução da origem. "
        "Proporções diferentes de 16:9 recebem tarjas pretas.\n"
        "• CRF: 20 (padrão) é o melhor equilíbrio. O YouTube recomprime tudo: num teste, CRF 16 gerou um "
        "arquivo 60% maior que o CRF 20 com diferença final imperceptível (0,2 dB). Use 23 para arquivos "
        "ainda menores (perda pequena) e evite 12–14, que só geram arquivos enormes. O arquivo codificado "
        "sempre fica maior que o original: o mosaico é muito mais difícil de comprimir.\n"
        "• Preset: 'slow' comprime melhor, 'veryfast' termina antes.\n\n"
        "DECODIFICAÇÃO:\n"
        "• Qualidade do YouTube: resolução máxima a baixar/transmitir.\n"
        "• Suavizar emendas: remove a grade fina que a compressão deixa entre os blocos."
    ),
    (
        "6. Segurança: o que esta cifra protege (e o que não protege)",
        "Isto é OFUSCAÇÃO, não criptografia forte. Ela impede que alguém assista ao vídeo casualmente, "
        "mas:\n"
        "• A mesma permutação vale para todos os quadros. Um atacante determinado pode tratar a imagem "
        "como um quebra-cabeça e remontar os blocos por continuidade de bordas, sem a senha.\n"
        "• A inversão espectral e o embaralhamento de fatias do áudio também podem ser revertidos com "
        "análise cuidadosa.\n"
        "• Não use para conteúdo cuja exposição traria dano real. Para isso, use um arquivo "
        "criptografado de verdade (ex.: 7-Zip AES-256) em um serviço de armazenamento privado."
    ),
    (
        "7. Responsabilidade e regras do YouTube",
        "• Envie apenas conteúdo que você tem direito de publicar. Embaralhar um vídeo não muda quem "
        "detém os direitos dele nem as regras da plataforma.\n"
        "• Grades muito finas viram um 'chuvisco' que exige bitrate altíssimo e pode ser tratado como "
        "arquivo corrompido; as grades oferecidas foram escolhidas para evitar isso.\n"
        "• Prefira publicar como 'Não listado' e compartilhar o link e a senha apenas com quem deve "
        "assistir."
    ),
    (
        "8. Linha de comando e solução de problemas",
        "CLI (útil para lotes):\n"
        "  python -m cipher_studio encode entrada.mp4 saida.mp4 --grid 32x18 --resolution 1080p\n"
        "  python -m cipher_studio decode \"https://youtu.be/...\" restaurado.mp4\n"
        "A senha vem da variável CIPHER_PASSWORD ou é pedida no terminal.\n\n"
        "PROBLEMAS COMUNS:\n"
        "• 'imagem ainda embaralhada': senha, grade ou inversão de cor diferentes das usadas no encode.\n"
        "• Erro do yt-dlp: atualize com 'pip install -U yt-dlp' (o YouTube muda com frequência).\n"
        "• 'CERTIFICATE_VERIFY_FAILED' (comum no macOS com o Python do python.org): rode "
        "'pip install -r requirements.txt' para instalar o certifi, que o programa usa automaticamente, "
        "e reabra o app. Para corrigir no sistema inteiro, execute uma vez o "
        "'Install Certificates.command' que fica em /Applications/Python 3.X/.\n"
        "• Sem som no player: verifique a saída de áudio padrão do sistema.\n"
        "• 'Fantasma' metálico do áudio embaralhado ao fundo: o vídeo foi codificado antes da "
        "versão 2.1. Codifique-o novamente e publique a nova versão (a imagem não muda; vídeos "
        "antigos continuam abrindo, mas o áudio deles não pode ser totalmente recuperado).\n"
        "• Reprodução engasgando: escolha 720p em Configurações ou diminua a janela."
    ),
]
