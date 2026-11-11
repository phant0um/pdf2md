"""
Testes para core/pptx_converter.py.
Fixtures criadas programaticamente via python-pptx (sem arquivos binários no repo).
"""
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Inches

from core.pptx_converter import _tabela_para_md, pptx_to_md

# ── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def pptx_basico(tmp_path) -> Path:
    """PPTX com 2 slides: texto simples."""
    prs = Presentation()
    layout = prs.slide_layouts[1]  # título + conteúdo

    slide1 = prs.slides.add_slide(layout)
    slide1.shapes.title.text = "Slide Um"
    slide1.placeholders[1].text = "Conteúdo do slide um."

    slide2 = prs.slides.add_slide(layout)
    slide2.shapes.title.text = "Slide Dois"
    slide2.placeholders[1].text = "Conteúdo do slide dois."

    path = tmp_path / "basico.pptx"
    prs.save(str(path))
    return path


@pytest.fixture
def pptx_com_tabela(tmp_path) -> Path:
    """PPTX com slide contendo tabela 2×3."""
    prs = Presentation()
    layout = prs.slide_layouts[5]  # em branco
    slide = prs.slides.add_slide(layout)

    rows, cols = 3, 2
    table = slide.shapes.add_table(rows, cols, Inches(1), Inches(1), Inches(6), Inches(3)).table
    dados = [["Nome", "Valor"], ["Alpha", "100"], ["Beta", "200"]]
    for r, row_data in enumerate(dados):
        for c, val in enumerate(row_data):
            table.cell(r, c).text = val

    path = tmp_path / "tabela.pptx"
    prs.save(str(path))
    return path


@pytest.fixture
def pptx_vazio(tmp_path) -> Path:
    """PPTX com slide sem conteúdo de texto."""
    prs = Presentation()
    layout = prs.slide_layouts[5]  # em branco
    prs.slides.add_slide(layout)
    path = tmp_path / "vazio.pptx"
    prs.save(str(path))
    return path


# ── Testes básicos ───────────────────────────────────────────────────────────

def test_pptx_dois_slides(pptx_basico):
    """Dois slides → dois headings ## e dois ### (títulos)."""
    md = pptx_to_md(pptx_basico)
    assert "## Slide 1" in md
    assert "## Slide 2" in md
    assert "### Slide Um" in md
    assert "### Slide Dois" in md


def test_pptx_conteudo_corpo(pptx_basico):
    """Conteúdo do corpo aparece no output."""
    md = pptx_to_md(pptx_basico)
    assert "Conteúdo do slide um." in md
    assert "Conteúdo do slide dois." in md


def test_pptx_tabela(pptx_com_tabela):
    """Tabela → formato Markdown com header + separador + linhas."""
    md = pptx_to_md(pptx_com_tabela)
    assert "| Nome | Valor |" in md
    assert "| --- | --- |" in md
    assert "| Alpha | 100 |" in md
    assert "| Beta | 200 |" in md


def test_pptx_vazio_nao_falha(pptx_vazio):
    """Slide sem texto não levanta exceção."""
    md = pptx_to_md(pptx_vazio)
    assert isinstance(md, str)


def test_pptx_arquivo_inexistente(tmp_path):
    """FileNotFoundError para arquivo inexistente."""
    with pytest.raises(FileNotFoundError):
        pptx_to_md(tmp_path / "nao_existe.pptx")


def test_pptx_extensao_errada(tmp_path):
    """ValueError para extensão incorreta."""
    f = tmp_path / "arquivo.pdf"
    f.write_bytes(b"%PDF-1.4")
    with pytest.raises(ValueError):
        pptx_to_md(f)


def test_pptx_corrompido(tmp_path):
    """RuntimeError para PPTX inválido."""
    f = tmp_path / "corrompido.pptx"
    f.write_bytes(b"nao_e_pptx")
    with pytest.raises(RuntimeError):
        pptx_to_md(f)


# ── Testes do helper _tabela_para_md ─────────────────────────────────────────

def test_tabela_para_md_pipe_em_celula(pptx_com_tabela):
    """Pipes dentro de células são escapados."""
    from pptx import Presentation

    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    table = slide.shapes.add_table(2, 2, Inches(1), Inches(1), Inches(6), Inches(2)).table
    table.cell(0, 0).text = "A|B"
    table.cell(0, 1).text = "C"
    table.cell(1, 0).text = "D"
    table.cell(1, 1).text = "E"

    md = _tabela_para_md(table)
    assert "A\\|B" in md


# ── Caixas de texto, grupos e notas do orador ───────────────────────────────

def test_pptx_caixa_de_texto_nao_placeholder(tmp_path):
    """Caixa de texto comum (não placeholder) não derruba a conversão; título continua ###."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])  # só título
    slide.shapes.title.text = "Titulo"
    caixa = slide.shapes.add_textbox(Inches(1), Inches(2), Inches(3), Inches(1))
    caixa.text_frame.text = "Texto na caixa solta"
    path = tmp_path / "caixa.pptx"
    prs.save(str(path))

    md = pptx_to_md(path)
    assert "Texto na caixa solta" in md
    assert "### Titulo" in md


def test_pptx_texto_dentro_de_grupo(tmp_path):
    """Texto de caixa dentro de grupo entra no Markdown."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    grupo = slide.shapes.add_group_shape()
    caixa = grupo.shapes.add_textbox(Inches(1), Inches(2), Inches(3), Inches(1))
    caixa.text_frame.text = "Texto do grupo"
    path = tmp_path / "grupo.pptx"
    prs.save(str(path))

    assert "Texto do grupo" in pptx_to_md(path)


def test_pptx_grupo_aninhado(tmp_path):
    """Grupo dentro de grupo: texto do nível mais interno entra no Markdown."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    externo = slide.shapes.add_group_shape()
    interno = externo.shapes.add_group_shape()
    caixa = interno.shapes.add_textbox(Inches(1), Inches(2), Inches(3), Inches(1))
    caixa.text_frame.text = "Texto aninhado"
    path = tmp_path / "aninhado.pptx"
    prs.save(str(path))

    assert "Texto aninhado" in pptx_to_md(path)


def test_pptx_notas_do_orador(tmp_path):
    """Notas com duas linhas viram bloco de citação após o corpo do slide, antes do próximo slide."""
    prs = Presentation()
    layout = prs.slide_layouts[1]
    slide1 = prs.slides.add_slide(layout)
    slide1.shapes.title.text = "Slide Um"
    slide1.placeholders[1].text = "Corpo um."
    slide1.notes_slide.notes_text_frame.text = "Primeira nota\nSegunda nota"
    slide2 = prs.slides.add_slide(layout)
    slide2.shapes.title.text = "Slide Dois"
    path = tmp_path / "notas.pptx"
    prs.save(str(path))

    md = pptx_to_md(path)
    bloco = "> **Notas do orador**\n> Primeira nota\n> Segunda nota"
    assert bloco in md
    assert md.index("Corpo um.") < md.index(bloco) < md.index("## Slide 2")


def test_pptx_sem_notas_sem_bloco(pptx_basico):
    """Apresentação sem notas não ganha bloco de notas."""
    assert "Notas do orador" not in pptx_to_md(pptx_basico)


def test_pptx_notas_vazias_sem_bloco(tmp_path):
    """Slide com notas criadas mas vazias não ganha bloco de notas."""
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[5])
    notas = slide.notes_slide  # cria a notes slide sem texto
    assert notas is not None
    path = tmp_path / "notas_vazias.pptx"
    prs.save(str(path))

    assert "Notas do orador" not in pptx_to_md(path)
