"""
Converte arquivos PDF em Markdown.
Detecta automaticamente páginas com imagem e aplica OCR.
Feature `--imagens` (PDF-only): extrai imagens embutidas como assets (ADR-0005).
"""
import importlib.util
import os
import tempfile
from pathlib import Path
from typing import Any

import fitz  # PyMuPDF
import pymupdf4llm

from core.image_assets import ColetorAssets, ContextoAssets
from core.image_converter import (
    _configurar_tesseract_cmd,
    alt_text_enxuto,
    image_to_md,
    ocr_bytes,
)
from core.pdf_images import extrair_imagens
from core.quality import reparar_ligaduras
from core.utils import (
    _MAX_BYTES_RENDER_PAGINA,
    ModoImagem,
    _validar_existencia,
)

# Mínimo de caracteres para considerar que uma página tem texto nativo suficiente.
# Abaixo disso, a página é tratada como imagem e passa por OCR.
_MIN_TEXTO_PAGINA = 50

# DPI para renderização de páginas sem texto nativo (para OCR).
_OCR_DPI = 300

# Margem vertical padrão (em % da altura da página) ignorada quando
# --ignorar-margens está ativo. Cobre cabeçalhos e rodapés comuns.
_MARGEM_PADRAO_PCT = 5.0

# pymupdf4llm >= 1.27 roda Tesseract por conta própria nas imagens embutidas
# (use_ocr=True é o default do caminho de layout). O projeto já tem OCR próprio
# controlado por ModoImagem — o do pymupdf4llm só duplica trabalho (~40% do
# tempo de extração) e produz texto pior que o embutido.
#
# `_use_layout` é privado: se o upstream renomear, caímos na detecção pública
# do módulo de layout em vez de desligar o fix em silêncio.
_LAYOUT_ATIVO: bool = getattr(
    pymupdf4llm,
    "_use_layout",
    importlib.util.find_spec("pymupdf.layout") is not None,
)
_KWARGS_EXTRACAO: dict[str, Any] = {"use_ocr": False} if _LAYOUT_ATIVO else {}


def pdf_to_md(
    path: Path,
    ignorar_margens: float = 0.0,
    modo_imagem: ModoImagem = ModoImagem.transcrever,
    assets_dir: Path | None = None,
    md_dir: Path | None = None,
    wikilinks: bool = False,
    prefixo_nome: str = "",
    avisos: list[str] | None = None,
) -> str:
    """
    Converte um arquivo PDF em string Markdown.

    Estratégia:
    1. Tenta extrair texto via pymupdf4llm.to_markdown()
    2. Para cada página sem texto (ou texto < 50 chars), aplica OCR via image_converter
    3. Retorna MD concatenado de todas as páginas

    Args:
        path: Caminho absoluto para o arquivo PDF. Deve existir.
        ignorar_margens: Percentual (0-100) das margens superior e inferior
            a ignorar. 0 = desativado (padrão). Ex: 5.0 ignora 5% do topo
            e 5% do rodapé de cada página (cabeçalhos e rodapés).
        modo_imagem: Política de imagens embutidas (ModoImagem).
            transcrever (default) é byte-idêntico ao comportamento atual.
        assets_dir: Diretório dos assets extraídos (D1). Default: `md_dir/<stem>_assets`.
        md_dir: Diretório do .md de saída — base dos links relativos.
            Default: pai de assets_dir.
        wikilinks: True em modo Obsidian → `![[nome]]` (D1). Assets em
            `assets_dir` (o batch resolve para vault/attachments).
        prefixo_nome: Prefixo dos nomes de asset — usado quando o diretório
            de assets é COMPARTILHADO entre documentos (--assets-dir ou
            Obsidian), evitando colisão de `img_p001_0.png` sob ThreadPool.
        avisos: Lista opcional onde avisos de extração são anexados
            (limites excedidos, imagens ilegíveis).

    Returns:
        String Markdown com conteúdo extraído.

    Raises:
        FileNotFoundError: Se path não existe.
        ValueError: Se path não é arquivo PDF válido.
        RuntimeError: Se pymupdf falha ao abrir o arquivo.
    """
    _validar_existencia(path)
    _configurar_tesseract_cmd()

    if path.suffix.lower() != ".pdf":
        raise ValueError("Arquivo não é PDF válido")

    contexto = None
    if modo_imagem != ModoImagem.transcrever:
        dir_assets = assets_dir or (path.parent / f"{path.stem}_assets")
        contexto = ContextoAssets(
            modo=modo_imagem,
            coletor=ColetorAssets(prefixo=prefixo_nome),
            assets_dir=dir_assets,
            md_dir=md_dir or dir_assets.parent,
            wikilinks=wikilinks,
            avisos=avisos if avisos is not None else [],
        )

    try:
        doc = fitz.open(str(path))
    except Exception as exc:
        raise RuntimeError("Falha ao abrir PDF — arquivo corrompido ou formato inválido") from exc

    # FIX 3: o fallback silencioso do pymupdf4llm perde headings/tabelas —
    # anexa aviso no mesmo canal que doc_converter usa (contexto.avisos).
    avisos_efetivos = contexto.avisos if contexto is not None else avisos
    chunks = _extrair_chunks_markdown(path, avisos_efetivos)

    try:
        bruto: list[str] = []
        partes = [
            _processar_pagina(doc, num, chunks, ignorar_margens, contexto, bruto)
            for num in range(len(doc))
        ]
    finally:
        doc.close()

    md = "\n\n".join(partes)
    md, n_reparos = reparar_ligaduras(md, "\n".join(bruto))
    if n_reparos and avisos_efetivos is not None:
        avisos_efetivos.append(
            f"{n_reparos} ligadura(s) tipográfica(s) reparada(s) "
            f"(glifo sem ToUnicode no PDF de origem)"
        )
    return md


def _extrair_chunks_markdown(path: Path, avisos: list[str] | None = None) -> list[dict[str, Any]]:
    """
    Extrai Markdown de todas as páginas em uma única passada do pymupdf4llm.

    Antes: to_markdown(str(path), pages=[n]) dentro do loop reabria e
    reparseava o PDF inteiro por página — O(n) parses do documento.

    Fallback graceful: se pymupdf4llm falhar (PDF corrompido, formato exótico),
    retorna lista vazia — o texto bruto do fitz ainda é usado por _processar_pagina.
    FIX 3: o fallback NÃO é mais silencioso — anexa aviso (headings/tabelas
    podem ser perdidos) para o usuário saber que a saída é degradada.
    """
    try:
        chunks = pymupdf4llm.to_markdown(str(path), page_chunks=True, **_KWARGS_EXTRACAO)
        if isinstance(chunks, list):
            return chunks
        _aviso_fallback_pymupdf(avisos)
        return []
    except Exception:
        _aviso_fallback_pymupdf(avisos)
        return []


def _aviso_fallback_pymupdf(avisos: list[str] | None) -> None:
    """Registra o aviso de fallback (FIX 3) — sem lista de avisos, silencioso."""
    if avisos is not None:
        avisos.append(
            "pymupdf4llm falhou ao extrair markdown estruturado — usando texto "
            "bruto do PyMuPDF (headings/tabelas podem ser perdidos)"
        )


def _processar_pagina(
    doc: fitz.Document,
    num_pagina: int,
    chunks: list[dict[str, Any]],
    ignorar_margens: float,
    contexto: ContextoAssets | None = None,
    bruto: list[str] | None = None,
) -> str:
    """Extrai conteúdo de uma página: texto nativo (filtrado ou não) ou OCR."""
    pagina = doc.load_page(num_pagina)
    texto_pagina = pagina.get_text()

    if bruto is not None:
        bruto.append(texto_pagina)

    if len(texto_pagina.strip()) >= _MIN_TEXTO_PAGINA:
        if ignorar_margens > 0:
            texto = _texto_filtrado_margens(pagina, ignorar_margens)
        else:
            texto = _texto_nativo_pagina(num_pagina, chunks, texto_pagina)
        if contexto is not None and contexto.modo in (ModoImagem.extrair, ModoImagem.ambos):
            texto = _anexar_imagens_embutidas(doc, num_pagina, texto, contexto)
        return texto

    # Página-scan (sem texto nativo): OCR do render.
    if contexto is not None and contexto.modo == ModoImagem.ignorar:
        return ""  # modo ignorar: descarta sem OCR (e sem render persistido)
    return _ocr_pagina(pagina, num_pagina, contexto)


def _texto_nativo_pagina(num_pagina: int, chunks: list[dict[str, Any]], texto_bruto: str) -> str:
    """Retorna o Markdown do chunk correspondente, com fallback para texto bruto."""
    if num_pagina < len(chunks):
        md = (chunks[num_pagina].get("text") or "").strip()
        if md:
            return md
    return texto_bruto.strip()


def _texto_filtrado_margens(pagina: fitz.Page, margem_pct: float) -> str:
    """Extrai texto da página ignorando blocos dentro da margem superior e inferior."""
    dados = pagina.get_text("dict")
    altura = pagina.rect.height
    margem_px = altura * (margem_pct / 100.0)
    topo_limite = margem_px
    rodape_limite = altura - margem_px

    partes: list[str] = []
    for block in dados.get("blocks", []):
        if block.get("type") != 0:  # 0 = texto, 1 = imagem
            continue
        bbox = block.get("bbox", (0, 0, 0, 0))
        y0, y1 = bbox[1], bbox[3]
        # Ignora blocks que estão majoritariamente dentro das margens
        if y0 < topo_limite or y1 > rodape_limite:
            continue
        for line in block.get("lines", []):
            linha = "".join(span.get("text", "") for span in line.get("spans", []))
            if linha.strip():
                partes.append(linha.strip())

    return "\n".join(partes)


def _ocr_pagina(
    pagina: fitz.Page, num_pagina: int = 0, contexto: ContextoAssets | None = None
) -> str:
    """Renderiza a página como imagem em alta resolução e aplica OCR.

    Em `extrair`/`ambos` (D3): persiste o render 300 dpi como
    `p{n:03d}_full.png` e anexa o link — `get_images()` num scan devolve
    a página inteira como 1 imagem; persistir o render evita duplicação
    de path e dá ao usuário o original da página.
    """
    pix = pagina.get_pixmap(dpi=_OCR_DPI)
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = Path(tmp.name)
        pix.save(str(tmp_path))

    try:
        texto = image_to_md(tmp_path)
    finally:
        tmp_path.unlink(missing_ok=True)

    if contexto is not None and contexto.modo in (ModoImagem.extrair, ModoImagem.ambos):
        texto = _persistir_render_pagina(pix, num_pagina, contexto, texto)

    return texto


def _persistir_render_pagina(
    pix: fitz.Pixmap, num_pagina: int, contexto: ContextoAssets, texto: str
) -> str:
    """Persiste o render da página-scan (D3) e anexa o link ao MD.

    Renders contam no limite anti resource-bomb do documento (coletor):
    um scan de 10k páginas não pode gravar 10k PNGs sem aviso.
    """
    from core.pdf_images import caminho_seguro, preparar_assets_dir

    if contexto.coletor.atingiu_limite():
        # FIX 4: guard anti-duplicação — 10k páginas-scan geravam 9.5k
        # avisos idênticos (mesmo padrão de doc_converter._montar_handler).
        if not contexto.aviso_limite_renders:
            contexto.avisos.append(
                "limite de imagens por documento excedido — renders de páginas-scan interrompidos"
            )
            contexto.aviso_limite_renders = True
        return texto

    dados = pix.tobytes("png")
    if len(dados) > _MAX_BYTES_RENDER_PAGINA:
        contexto.avisos.append(
            f"render da página {num_pagina + 1} excede "
            f"{_MAX_BYTES_RENDER_PAGINA // (1024 * 1024)} MB — não persistido"
        )
        return texto

    # Valida/cria o diretório (mesmas regras dos assets: symlink recusado)
    dir_assets = preparar_assets_dir(contexto.assets_dir)

    nome = f"{contexto.coletor.prefixo}p{num_pagina + 1:03d}_full.png"
    caminho = caminho_seguro(dir_assets, nome)
    caminho.write_bytes(dados)
    contexto.coletor.total += 1

    return texto.rstrip() + "\n\n" + _link_arquivo(nome, caminho, contexto, f"página {num_pagina + 1}")


def _anexar_imagens_embutidas(
    doc: fitz.Document, num_pagina: int, texto: str, contexto: ContextoAssets
) -> str:
    """Extrai as imagens da página e anexa os links ao final do MD."""
    assets, avisos = extrair_imagens(doc, num_pagina, contexto.assets_dir, contexto.coletor)
    contexto.avisos.extend(avisos)
    if not assets:
        return texto

    links: list[str] = []
    for asset in assets:
        alt = "imagem"
        if contexto.modo == ModoImagem.ambos and not asset.duplicado:
            alt = alt_text_enxuto(ocr_bytes(asset.dados, asset.extensao))
        links.append(_link_arquivo(asset.nome, asset.caminho_disco, contexto, alt))

    return texto.rstrip() + "\n\n" + "\n\n".join(links)


def _link_arquivo(nome: str, caminho_disco: Path, contexto: ContextoAssets, alt: str) -> str:
    """Monta o link do asset: wikilink Obsidian ou ![]() relativo ao MD (D1)."""
    if contexto.wikilinks:
        return f"![[{nome}]]"
    # md_dir precisa estar resolvido: no macOS /var é symlink de /private/var
    # e os caminhos dos assets já saem resolvidos — mistura viraria relpath gigante.
    rel = os.path.relpath(caminho_disco, contexto.md_dir.resolve())
    return f"![{alt}]({rel})"
