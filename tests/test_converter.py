"""Testes para core/converter.py."""
from pathlib import Path
from unittest.mock import patch

import fitz
import pytest
from PIL import Image, ImageDraw

from core.converter import pdf_to_md
from core.utils import ModoImagem


def test_pdf_texto_simples(fixture_texto_simples):
    """PDF com texto → MD com conteúdo."""
    md = pdf_to_md(fixture_texto_simples)
    assert "documento de teste" in md.lower() or len(md) > 20


def test_pdf_escaneado_ocr(fixture_escaneado, mock_tesseract):
    """PDF imagem → MD via OCR (requer Tesseract)."""
    md = pdf_to_md(fixture_escaneado)
    assert "OCR" in md or "escaneado" in md.lower() or len(md) > 10


def test_pdf_nao_existe():
    """FileNotFoundError para PDF inexistente."""
    with pytest.raises(FileNotFoundError):
        pdf_to_md(Path("/caminho/que/nao/existe.pdf"))


def test_pdf_corrompido(tmp_path):
    """RuntimeError para PDF corrompido."""
    path = tmp_path / "corrompido.pdf"
    path.write_text("isto não é um pdf", encoding="utf-8")
    with pytest.raises(RuntimeError):
        pdf_to_md(path)


def test_pdf_vazio(fixture_vazio):
    """Retorna string vazia ou MD mínimo para PDF vazio."""
    md = pdf_to_md(fixture_vazio)
    assert md.strip() == "" or len(md.strip()) < 100


def test_pdf_tabela(fixture_tabela):
    """PDF com tabela → MD com conteúdo."""
    md = pdf_to_md(fixture_tabela)
    assert len(md) > 10


def test_pdf_multi_coluna(fixture_multi_coluna):
    """PDF multi-coluna → MD com conteúdo."""
    md = pdf_to_md(fixture_multi_coluna)
    assert len(md) > 20


def test_pdf_misto_texto_imagem(tmp_path, mock_tesseract):
    """Páginas texto + páginas imagem → MD completo."""
    path = tmp_path / "misto.pdf"
    doc = fitz.open()
    # Página 1: texto
    p1 = doc.new_page(width=595, height=842)
    p1.insert_text((50, 50), "Página com texto normal.")
    # Página 2: imagem (simulada)
    img = Image.new("RGB", (400, 200), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 80), "Página escaneada", fill="black")
    img_path = tmp_path / "misto_img.png"
    img.save(img_path)
    p2 = doc.new_page(width=400, height=200)
    p2.insert_image(fitz.Rect(0, 0, 400, 200), filename=str(img_path))
    doc.save(str(path))
    doc.close()

    md = pdf_to_md(path)
    assert len(md) > 20


# ── Feature --imagens (T3/T5) ────────────────────────────────────────────────

def _pdf_texto_com_imagens(tmp_path: Path, qtd: int = 2) -> Path:
    """PDF com texto nativo + `qtd` imagens embutidas."""
    path = tmp_path / "imagens.pdf"
    doc = fitz.open()
    pagina = doc.new_page(width=595, height=842)
    pagina.insert_text(
        (50, 150),
        "Página com texto nativo e imagens embutidas para testar a extração de assets.",
    )
    for i in range(qtd):
        img = Image.new("RGB", (40, 40), color=((i * 90) % 255, 60, 120))
        img_path = tmp_path / f"emb_{i}.png"
        img.save(img_path)
        pagina.insert_image(fitz.Rect(50 + i * 60, 50, 90 + i * 60, 90), filename=str(img_path))
    doc.save(str(path))
    doc.close()
    return path


def test_pdf_imagens_extrair_gera_assets_e_links(tmp_path):
    """Modo extrair: 2 PNGs em disco + 2 links ![]() no MD."""
    pdf = _pdf_texto_com_imagens(tmp_path, qtd=2)
    saida = tmp_path / "out"
    assets = saida / "imagens_assets"
    saida.mkdir()

    md = pdf_to_md(pdf, modo_imagem=ModoImagem.extrair, assets_dir=assets, md_dir=saida)

    assert md.count("![imagem](") == 2
    assert "imagens_assets/img_p001_0.png" in md
    assert "imagens_assets/img_p001_1.png" in md
    assert len(list(assets.iterdir())) == 2


def test_pdf_default_byte_identico_e_sem_assets(tmp_path):
    """Default = transcrever: byte-idêntico ao atual e nada escrito em disco."""
    pdf = _pdf_texto_com_imagens(tmp_path)

    md_default = pdf_to_md(pdf)
    md_explicito = pdf_to_md(pdf, modo_imagem=ModoImagem.transcrever)

    assert md_default == md_explicito
    assert not (tmp_path / "imagens_assets").exists()


def test_pdf_imagens_ambos_ocr_como_alt_text(tmp_path):
    """Modo ambos: OCR da imagem vira alt-text do link."""
    pdf = _pdf_texto_com_imagens(tmp_path, qtd=1)

    with patch("core.image_converter.image_to_md", return_value="TEXTO OCR DA IMAGEM"):
        md = pdf_to_md(pdf, modo_imagem=ModoImagem.ambos)

    assert "![TEXTO OCR DA IMAGEM](imagens_assets/img_p001_0.png)" in md


def test_pdf_scan_extrair_persiste_render(tmp_path, mock_tesseract):
    """Página-scan em extrair: render 300dpi persistido como p001_full.png (D3)."""
    path = tmp_path / "scan.pdf"
    img = Image.new("RGB", (400, 200), color="white")
    draw = ImageDraw.Draw(img)
    draw.text((10, 80), "Página escaneada", fill="black")
    img_path = tmp_path / "scan.png"
    img.save(img_path)
    doc = fitz.open()
    pagina = doc.new_page(width=400, height=200)
    pagina.insert_image(fitz.Rect(0, 0, 400, 200), filename=str(img_path))
    doc.save(str(path))
    doc.close()

    saida = tmp_path / "out"
    assets = saida / "scan_assets"
    saida.mkdir()

    md = pdf_to_md(path, modo_imagem=ModoImagem.extrair, assets_dir=assets, md_dir=saida)

    assert (assets / "p001_full.png").exists()
    assert "![página 1](scan_assets/p001_full.png)" in md


def test_pdf_imagens_ignorar_descarta_sem_ocr(tmp_path, monkeypatch):
    """Modo ignorar em página-scan: sem OCR e sem render persistido."""
    chamadas_ocr: list = []

    def spy(image, lang=None, config=None):
        chamadas_ocr.append(image)
        return "Texto OCR mockado"

    monkeypatch.setattr("pytesseract.image_to_string", spy)
    monkeypatch.setattr("core.image_converter.verificar_tesseract", lambda: True)

    path = tmp_path / "scan.pdf"
    img = Image.new("RGB", (400, 200), color="white")
    img_path = tmp_path / "scan.png"
    img.save(img_path)
    doc = fitz.open()
    pagina = doc.new_page(width=400, height=200)
    pagina.insert_image(fitz.Rect(0, 0, 400, 200), filename=str(img_path))
    doc.save(str(path))
    doc.close()

    md = pdf_to_md(path, modo_imagem=ModoImagem.ignorar)

    assert md.strip() == ""
    assert chamadas_ocr == []
    assert not (tmp_path / "scan_assets").exists()


def test_pdf_imagens_obsidian_wikilink_e_attachments(tmp_path):
    """Modo obsidian (D1): wikilink ![[...]] + arquivo na pasta de attachments."""
    pdf = _pdf_texto_com_imagens(tmp_path, qtd=1)
    vault = tmp_path / "vault"
    (vault / "attachments").mkdir(parents=True)

    md = pdf_to_md(
        pdf,
        modo_imagem=ModoImagem.extrair,
        assets_dir=vault / "attachments",
        md_dir=vault,
        wikilinks=True,
        prefixo_nome="imagens__",
    )

    assert "![[imagens__img_p001_0.png]]" in md
    assert (vault / "attachments" / "imagens__img_p001_0.png").exists()


# ── Funções extraídas (Ciclo 9) ──────────────────────────────────────────────

def test_extrair_chunks_retorna_lista(fixture_texto_simples):
    """_extrair_chunks_markdown retorna list[dict] para PDF válido."""
    from core.converter import _extrair_chunks_markdown
    chunks = _extrair_chunks_markdown(fixture_texto_simples)
    assert isinstance(chunks, list)
    assert len(chunks) > 0


def test_extrair_chunks_fallback_graceful(tmp_path):
    """_extrair_chunks_markdown retorna [] para arquivo não-PDF sem levantar."""
    from core.converter import _extrair_chunks_markdown
    path = tmp_path / "nao_pdf.pdf"
    path.write_bytes(b"dados invalidos")
    chunks = _extrair_chunks_markdown(path)
    assert chunks == []


def test_converter_arquivo_desconhecido_retorna_none(tmp_path):
    """_converter_arquivo retorna None para extensão não suportada."""
    from core.batch import _converter_arquivo
    path = tmp_path / "arquivo.xyz"
    path.write_text("dados")
    assert _converter_arquivo(path) is None


def test_converter_arquivo_pdf_retorna_str(fixture_texto_simples):
    """_converter_arquivo retorna str (markdown) para PDF válido."""
    from core.batch import _converter_arquivo
    resultado = _converter_arquivo(fixture_texto_simples)
    assert isinstance(resultado, str)
    assert len(resultado) > 0


# ── Ignorar margens (cabeçalho/rodapé) ────────────────────────────────────────

def test_pdf_ignorar_margens_remove_topo_rodape(tmp_path):
    """--ignorar-margens remove texto do cabeçalho e rodapé da página."""
    path = tmp_path / "com_margens.pdf"
    doc = fitz.open()
    p = doc.new_page(width=595, height=842)
    # Cabeçalho no topo (y=20)
    p.insert_text((50, 20), "CABECALHO DO DOCUMENTO")
    # Corpo no meio (y=400)
    p.insert_text((50, 400), "Conteudo principal do documento.")
    # Rodapé embaixo (y=820)
    p.insert_text((50, 820), "RODAPE PAGINA 1")
    doc.save(str(path))
    doc.close()

    # Sem ignorar margens — tudo aparece
    md_completo = pdf_to_md(path)
    assert "CABECALHO" in md_completo.upper()
    assert "RODAPE" in md_completo.upper()

    # Com ignorar_margens=10 — cabeçalho e rodapé removidos
    md_filtrado = pdf_to_md(path, ignorar_margens=10.0)
    assert "CABECALHO" not in md_filtrado.upper()
    assert "RODAPE" not in md_filtrado.upper()
    assert "conteudo principal" in md_filtrado.lower()


def test_pdf_ignorar_margens_zero_nao_filtra(fixture_texto_simples):
    """ignorar_margens=0 (padrão) não filtra nada — igual a sem a opção."""
    md_normal = pdf_to_md(fixture_texto_simples)
    md_sem_margem = pdf_to_md(fixture_texto_simples, ignorar_margens=0.0)
    assert md_normal == md_sem_margem


# ── Reparo de ligaduras (U+FFFD) ─────────────────────────────────────────────

def test_pdf_sem_fffd_permanece_identico(fixture_texto_simples):
    """PDF sem U+FFFD: output contém texto esperado (não é corrompido)."""
    md = pdf_to_md(fixture_texto_simples)
    assert "documento de teste" in md.lower()


def test_pdf_reparo_ligadura_emite_aviso(tmp_path, monkeypatch):
    """pdf_to_md repara FFFD do chunk usando o texto cru da própria página."""
    import fitz

    from core import converter

    pdf = tmp_path / "ligadura.pdf"
    doc = fitz.open()
    pagina = doc.new_page()
    pagina.insert_text((50, 50), "Workflow reflect texto de referencia do documento")
    pagina.insert_text((50, 80), "linha extra para passar do minimo de caracteres")
    doc.save(str(pdf))
    doc.close()

    monkeypatch.setattr(
        converter,
        "_extrair_chunks_markdown",
        lambda path, avisos=None: [{"text": "Work\uFFFDow re\uFFFDect"}],
    )

    avisos: list[str] = []
    md = converter.pdf_to_md(pdf, avisos=avisos)

    assert "Workflow" in md
    assert "reflect" in md
    assert "\ufffd" not in md
    assert any("ligadura" in a for a in avisos)
