"""
Converte arquivos .pptx em Markdown.
Estratégia: python-pptx → extrai título + corpo + tabelas por slide.
"""
from pathlib import Path
from typing import Any

from core.utils import _linha_tabela_md, _sanitizar_celula_md, _validar_existencia


def pptx_to_md(path: Path) -> str:
    """
    Converte arquivo .pptx em Markdown.

    Estratégia:
    - Cada slide → seção (## Slide N)
    - Placeholder título (idx=0) → heading (### texto)
    - Corpo → parágrafos não-vazios preservados
    - Tabelas → tabela Markdown
    - Caixas de texto soltas (não placeholder) → parágrafos, sem heading
    - Grupos → shapes internos extraídos em ordem, recursivamente
    - Notas do orador → bloco de citação "Notas do orador" após o corpo do slide
    - Imagens → omitidas (sem OCR; usar image_converter separadamente se necessário)

    Args:
        path: Caminho absoluto para o arquivo .pptx. Deve existir.

    Returns:
        String Markdown com conteúdo extraído de todos os slides.

    Raises:
        FileNotFoundError: Se path não existe.
        ValueError: Se path não é arquivo .pptx válido.
        RuntimeError: Se python-pptx falha ao abrir o arquivo.
    """
    from pptx import Presentation  # lazy import — evita custo de import no startup

    _validar_existencia(path)
    if path.suffix.lower() != ".pptx":
        raise ValueError("Arquivo não é PPTX válido")

    try:
        prs = Presentation(str(path))
    except Exception as exc:
        raise RuntimeError(
            "Falha ao abrir PPTX — arquivo corrompido ou formato inválido"
        ) from exc

    partes: list[str] = []

    for num_slide, slide in enumerate(prs.slides, 1):
        partes.append(f"## Slide {num_slide}")
        for shape in slide.shapes:
            _processar_shape(shape, partes)
        _processar_notas(slide, partes)

    return "\n\n".join(partes)


def _processar_shape(shape: Any, partes: list[str]) -> None:
    """Extrai conteúdo de um shape (grupo, tabela, título ou corpo) para a lista de partes."""
    from pptx.enum.shapes import MSO_SHAPE_TYPE  # lazy import, como em pptx_to_md

    if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
        for interno in shape.shapes:
            _processar_shape(interno, partes)
        return

    if shape.has_table:
        md_tabela = _tabela_para_md(shape.table)
        if md_tabela:
            partes.append(md_tabela)
        return

    if not shape.has_text_frame:
        return

    texto_shape = shape.text_frame.text.strip()
    if not texto_shape:
        return

    # placeholder_format levanta ValueError em caixa de texto comum, por isso is_placeholder primeiro
    eh_titulo = shape.is_placeholder and shape.placeholder_format.idx == 0

    if eh_titulo:
        partes.append(f"### {texto_shape}")
    else:
        for para in shape.text_frame.paragraphs:
            linha = para.text.strip()
            if linha:
                partes.append(linha)


def _processar_notas(slide: Any, partes: list[str]) -> None:
    """Adiciona as notas do orador do slide como bloco de citação, se houver texto."""
    # has_notes_slide antes de notes_slide: o acesso cria a notes slide se ela não existe
    if not slide.has_notes_slide:
        return
    frame = slide.notes_slide.notes_text_frame
    if frame is None:
        return

    linhas = [para.text.strip() for para in frame.paragraphs if para.text.strip()]
    if not linhas:
        return

    bloco = ["> **Notas do orador**"] + [f"> {linha}" for linha in linhas]
    partes.append("\n".join(bloco))


def _tabela_para_md(table: Any) -> str:
    """Converte python-pptx Table em tabela Markdown."""
    if not table.rows:
        return ""

    linhas: list[str] = []
    for i, row in enumerate(table.rows):
        celulas = [_sanitizar_celula_md(cell.text) for cell in row.cells]
        linhas.append(_linha_tabela_md(celulas))
        if i == 0:
            linhas.append(_linha_tabela_md(["---"] * len(celulas)))

    return "\n".join(linhas)
