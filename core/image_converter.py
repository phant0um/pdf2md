"""
Converte arquivos de imagem em Markdown via OCR (Tesseract).
Suporta: PNG, JPG, JPEG, TIFF, WEBP, BMP, HEIC.
Pipeline: carrega imagem → pré-processa → OCR → retorna texto como MD.
"""
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

from PIL import Image
from pillow_heif import register_heif_opener

from core.utils import EXTENSOES_IMAGEM, _validar_existencia, _validar_extensao  # fonte única

if TYPE_CHECKING:
    from core.llm_enhancer import ConfigLLM

register_heif_opener()

# Paths comuns do Tesseract no macOS — necessário em apps PyInstaller,
# onde o PATH do processo não inclui /opt/homebrew/bin/
_TESSERACT_PATHS_MACOS = [
    "/opt/homebrew/bin/tesseract",   # Homebrew Apple Silicon (M1/M2/M3)
    "/usr/local/bin/tesseract",      # Homebrew Intel
    "/usr/bin/tesseract",            # instalação manual
]

# Largura mínima para não redimensionar — imagens menores passam por
# upscale 2x para melhorar a qualidade do OCR.
_MIN_LARGURA_OCR = 1000


@lru_cache(maxsize=1)
def _configurar_tesseract_cmd() -> None:
    """
    Configura pytesseract.tesseract_cmd para o path correto no macOS.
    Necessário em binários PyInstaller onde PATH é mínimo (sem /opt/homebrew/bin/).
    Memoizado: roda só uma vez por processo (shutil.which + stats não se repetem
    a cada página/imagem do batch).
    """
    import shutil

    import pytesseract

    # Já acessível no PATH atual — não precisa configurar
    if shutil.which("tesseract"):
        return

    # Procura nos paths conhecidos do Homebrew
    for caminho in _TESSERACT_PATHS_MACOS:
        if Path(caminho).exists():
            pytesseract.pytesseract.tesseract_cmd = caminho
            return


@lru_cache(maxsize=1)
def verificar_tesseract() -> bool:
    """
    Verifica se Tesseract está instalado e acessível.
    Configura o path automaticamente para binários PyInstaller no macOS.
    Retorna True se disponível, False caso contrário. Não lança exceção.
    Memoizado: evita um subprocess `tesseract --version` por página/imagem
    (a disponibilidade do Tesseract não muda durante a execução).
    """
    try:
        import pytesseract
        _configurar_tesseract_cmd()
        pytesseract.get_tesseract_version()
        return True
    except Exception:
        return False


def image_to_md(path: Path, visao: "ConfigLLM | None" = None) -> str:
    """
    Converte arquivo de imagem em string Markdown via OCR.

    Pipeline de pré-processamento (melhora qualidade OCR):
    1. Carrega imagem (Pillow; HEIC via pillow-heif)
    2. Converte para escala de cinza
    3. Redimensiona se largura < 1000px (scale 2x — melhora OCR em imagens pequenas)
    4. OCR via pytesseract com lang='por+eng'

    Resultado: texto extraído cru, sem formatação Markdown adicional.

    Args:
        path: Caminho para arquivo de imagem. Extensão deve estar em EXTENSOES_IMAGEM.
        visao: Config do LLM com visão para o fallback de OCR. None (padrão) =
            nunca chama o LLM; só o Tesseract. Passado só com opt-in explícito
            (`--llm` / `--llm-fallback`), usando o mesmo `--llm-url`/`--llm-modelo`.

    Returns:
        String Markdown com texto extraído. Retorna string vazia se imagem sem texto.

    Raises:
        FileNotFoundError: Se path não existe.
        ValueError: Se extensão não está em EXTENSOES_IMAGEM.
        RuntimeError: Se Tesseract não está instalado.
    """
    _validar_existencia(path)
    _validar_extensao(path, EXTENSOES_IMAGEM)

    if not verificar_tesseract():
        raise RuntimeError(
            "Tesseract não encontrado. "
            "Instale com: brew install tesseract tesseract-lang"
        )

    import pytesseract

    try:
        with Image.open(path) as img:
            # Converte para escala de cinza
            img_cinza = img.convert("L")

            # Redimensiona se largura < 1000px
            largura, altura = img_cinza.size
            if largura < _MIN_LARGURA_OCR:
                img_cinza = img_cinza.resize(
                    (largura * 2, altura * 2), Image.Resampling.LANCZOS
                )

            # OCR
            texto: str = pytesseract.image_to_string(img_cinza, lang="por+eng")
    except pytesseract.TesseractError as exc:
        # FIX 7: TesseractError (ex: pacote de idioma ausente) NÃO é arquivo
        # corrompido — a mensagem antiga ("verifique se o arquivo está
        # corrompido") levava o usuário para o diagnóstico errado. Sem
        # str(exc) (pode conter path do tessdata — CWE-209).
        raise RuntimeError(
            "Falha no OCR — Tesseract não conseguiu processar a imagem "
            "(idioma ausente?). Instale: brew install tesseract tesseract-lang"
        ) from exc
    except Exception as exc:
        raise RuntimeError("Falha no processamento da imagem — verifique se o arquivo está corrompido") from exc

    texto = texto.strip()

    # Fallback: se Tesseract retornou texto muito curto e o usuário optou pelo LLM
    # com visão (`visao` não é None), tenta OCR via LLM (fontes incomuns, manuscrito).
    # Sem opt-in o LLM nunca é consultado: imagem/página pode ser dado sensível.
    if visao is not None and len(texto) < 10:
        from core.llm_enhancer import disponivel as llm_disponivel
        from core.llm_enhancer import ocr_com_visao

        if llm_disponivel(visao):
            texto_llm, _ = ocr_com_visao(path, visao)
            if texto_llm and len(texto_llm) > len(texto):
                return texto_llm

    return texto


def ocr_bytes(dados: bytes, extensao: str) -> str:
    """
    OCR de bytes de imagem via imagem temporária (modo `ambos` do --imagens).

    Alt-text para os links de imagem extraída de PDF/DOCX: reusa o mesmo
    pipeline de image_to_md em arquivo temporário, apagado no finally.

    Formatos fora de EXTENSOES_IMAGEM (ex.: GIF/PPM extraídos de PDF) são
    normalizados para PNG via Pillow. Qualquer falha retorna "" — o
    alt-text degrada para "imagem", nunca derruba a conversão.
    """
    # Alt-text nunca vai para o LLM: image_to_md é chamado sem `visao` (opt-in é por página/arquivo).
    import tempfile

    try:
        if extensao.lower() not in EXTENSOES_IMAGEM:
            dados = _normalizar_png(dados)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp_path.write_bytes(dados)

        try:
            return image_to_md(tmp_path).strip()
        finally:
            tmp_path.unlink(missing_ok=True)
    except Exception:
        return ""


def _normalizar_png(dados: bytes) -> bytes:
    """Reconverte bytes de imagem para PNG via Pillow (formato não suportado)."""
    import io

    with Image.open(io.BytesIO(dados)) as img:
        saida = io.BytesIO()
        img.save(saida, format="PNG")
        return saida.getvalue()


def alt_text_enxuto(ocr: str, max_chars: int = 120) -> str:
    """Alt-text enxuto: primeira linha não vazia, truncada."""
    for linha in ocr.splitlines():
        if linha.strip():
            limpa = linha.strip()
            return limpa[:max_chars] + ("…" if len(limpa) > max_chars else "")
    return "imagem"
