"""
Testes de glifos TeX com PDF sintético de verdade (MuPDF, sem mock).

Motivo: os testes com fake de get_text provam a lógica de pareamento, mas não
provam que o MuPDF produz o U+FFFD do gancho. Aqui o PDF é montado em memória
no tmp_path, sem binário versionado, e a condição é a real: fonte TeX
LMMathItalic sem ToUnicode, com /Differences mapeando 0x2C para um nome de
glifo fora da Adobe Glyph List, seguida de → de outra fonte.

Por que Type1 não embutida e não Type3: com Type3 e nome `lhookx`, o MuPDF
devolveu o próprio código (',') e não U+FFFD; a condição não se reproduzia.
Com Type1 não embutida, o MuPDF devolve U+FFFD sem a flag CID. Verificado.
"""

from pathlib import Path

import fitz
import pytest

from core.converter import pdf_to_md
from core.glifos_tex import extrair_glifos_tex

_TEXTO_APOIO = b"Texto de apoio para a pagina passar do limiar de OCR do conversor."
# HOW: glifo da seta precisa de geometria (um traço). Com procedimento vazio o
# pymupdf4llm descarta o caractere e o teste não exercita o caso real.
_GLIFO_SETA = b"500 0 d0 0 250 m 500 250 l S\n"


def _stream(dados: bytes) -> bytes:
    """Stream sem filtro: /Length tem de bater com o tamanho exato dos bytes."""
    return b"<< /Length %d >>\nstream\n" % len(dados) + dados + b"\nendstream"


def _montar_pdf(
    base_font: str, seta_na_fonte_do_gancho: bool = False, texto_em_volta: bool = False
) -> bytes:
    """
    Monta um PDF de uma página com xref calculado à mão.

    Objetos: 1 catálogo, 2 páginas, 3 página, 4 content stream, 5 fonte TeX
    (Type1 não embutida, sem ToUnicode) com /Encoding 7, 6 fonte da seta
    (CMSY10 com arrowright), 7 encoding com 0x2C -> lhookx, 8 Helvetica de apoio
    (só para o texto passar do limiar), 9 encoding da seta.

    seta_na_fonte_do_gancho: a seta (0x41) vai na mesma fonte do gancho, em vez
    de CMSY10. HOW: é o layout em que o MuPDF junta `�→` num span só sem a
    flag CID e o separa com ela; o pareamento por span quebrava aqui.

    texto_em_volta: só vale com a seta em CMSY10. Põe "Seja f: A " antes e
    " B uma inclusao." depois, na mesma linha, como num parágrafo real.
    """
    if seta_na_fonte_do_gancho:
        conteudo = b"BT /F1 12 Tf 10 60 Td <2C41> Tj ET "
        codificacao_hook = b"<< /Type /Encoding /Differences [44 /lhookx 65 /arrowright] >>"
        ultimo_char = 65
    else:
        # HOW: texto Helvetica antes e depois, na mesma linha de base, como num
        # parágrafo real; o caso isolado (sem texto) não reproduz a quebra de span.
        if texto_em_volta:
            conteudo = (
                b"BT /F3 12 Tf 10 60 Td (Seja f: A ) Tj /F1 12 Tf <2C> Tj "
                b"/F2 12 Tf <41> Tj /F3 12 Tf ( B uma inclusao.) Tj ET "
            )
        else:
            conteudo = b"BT /F1 12 Tf 10 60 Td <2C> Tj /F2 12 Tf <41> Tj ET "
        codificacao_hook = b"<< /Type /Encoding /Differences [44 /lhookx] >>"
        ultimo_char = 44
    conteudo += b"BT /F3 12 Tf 10 30 Td (" + _TEXTO_APOIO + b") Tj ET"
    objs: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 120] /Contents 4 0 R "
            b"/Resources << /Font << /F1 5 0 R /F2 6 0 R /F3 8 0 R >> >> >>"
        ),
        _stream(conteudo),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /" + base_font.encode()
        + b" /Encoding 7 0 R /FirstChar 44 /LastChar " + str(ultimo_char).encode()
        + b" /Widths [" + b" ".join([b"500"] * (ultimo_char - 43)) + b"] >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /CMSY10 /Encoding 9 0 R "
        b"/FirstChar 65 /LastChar 65 /Widths [790] >>",
        codificacao_hook,
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Encoding /Differences [65 /arrowright] >>",
    ]
    # HOW: cada offset é a posição do "N 0 obj" no arquivo; o xref precisa dele
    # exato, senão o MuPDF reconstrói o arquivo e a condição muda sem aviso.
    corpo = b"%PDF-1.4\n"
    offsets: list[int] = []
    for numero, obj in enumerate(objs, start=1):
        offsets.append(len(corpo))
        corpo += b"%d 0 obj\n" % numero + obj + b"\nendobj\n"
    xref_pos = len(corpo)
    corpo += b"xref\n0 %d\n" % (len(objs) + 1)
    corpo += b"0000000000 65535 f \n"
    for off in offsets:
        corpo += b"%010d 00000 n \n" % off
    corpo += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        xref_pos,
    )
    return corpo


@pytest.fixture
def pdf_gancho(tmp_path: Path) -> Path:
    """PDF com gancho TeX sem ToUnicode seguido de seta, em tmp_path."""
    caminho = tmp_path / "gancho.pdf"
    caminho.write_bytes(_montar_pdf("LMMathItalic10-Regular"))
    return caminho


@pytest.fixture
def pdf_gancho_seta_mesma_fonte(tmp_path: Path) -> Path:
    """PDF com gancho e seta na mesma fonte LMMathItalic, sem ToUnicode."""
    caminho = tmp_path / "gancho_seta_mesma_fonte.pdf"
    caminho.write_bytes(_montar_pdf("LMMathItalic10-Regular", seta_na_fonte_do_gancho=True))
    return caminho


@pytest.fixture
def pdf_gancho_com_subset(tmp_path: Path) -> Path:
    """Mesmo PDF com prefixo de subset (ABCDEF+), como pdfTeX costuma gravar."""
    caminho = tmp_path / "gancho_subset.pdf"
    caminho.write_bytes(_montar_pdf("ABCDEF+LMMathItalic10-Regular"))
    return caminho


def test_mupdf_sem_cid_devolve_fffd_no_gancho(pdf_gancho: Path) -> None:
    """Sem a flag CID o gancho vira U+FFFD: é a condição que o módulo precisa tratar."""
    with fitz.open(str(pdf_gancho)) as doc:
        flags = fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_CID_FOR_UNKNOWN_UNICODE
        cru = doc[0].get_text("rawdict", flags=flags)
    chars = [
        c["c"]
        for bloco in cru["blocks"]
        if bloco.get("type") == 0
        for linha in bloco["lines"]
        for span in linha["spans"]
        if span["font"].startswith("LMMathItalic")
        for c in span["chars"]
    ]
    assert chars == ["�"]


def test_extrair_glifos_pdf_real_pega_gancho_com_seta(pdf_gancho: Path) -> None:
    """Gancho do PDF real sai como (LMMathItalic, 0x2C, →): a seta está no span seguinte."""
    with fitz.open(str(pdf_gancho)) as doc:
        glifos = extrair_glifos_tex(doc)
    assert glifos == [("LMMathItalic10-Regular", 0x2C, "→")]


@pytest.mark.parametrize("pdf_fixture", ["pdf_gancho", "pdf_gancho_com_subset"])
def test_fonte_com_prefixo_de_subset_continua_casando(pdf_fixture: str, request) -> None:
    """MuPDF tira o prefixo ABCDEF+ do nome do span: startswith("LMMathItalic") vale."""
    caminho = request.getfixturevalue(pdf_fixture)
    with fitz.open(str(caminho)) as doc:
        glifos = extrair_glifos_tex(doc)
    assert len(glifos) == 1
    fonte, codigo, proximo = glifos[0]
    assert fonte.startswith("LMMathItalic")
    assert (codigo, proximo) == (0x2C, "→")


def test_extrair_glifos_pdf_real_seta_na_fonte_do_gancho(pdf_gancho_seta_mesma_fonte: Path) -> None:
    """Seta na mesma fonte do gancho: a extração ainda liga o gancho à seta.

    Motivo: sem a flag CID o MuPDF junta `�→` num span; com ela, separa em dois.
    O pareamento por linha (char a char) tem que sobreviver às duas divisões.
    """
    with fitz.open(str(pdf_gancho_seta_mesma_fonte)) as doc:
        glifos = extrair_glifos_tex(doc)
    assert glifos == [("LMMathItalic10-Regular", 0x2C, "→")]


def test_pdf_to_md_troca_gancho_por_hook_linha(pdf_gancho_seta_mesma_fonte: Path) -> None:
    """Conversão ponta a ponta com a seta na fonte do gancho: `�→` vira ↪ e o U+FFFD some.

    Este é o layout em que `�→` aparece no Markdown (o de CMSY10 sai `→ _�_`,
    fora do escopo deste reparo).
    """
    md = pdf_to_md(pdf_gancho_seta_mesma_fonte)
    assert "↪" in md
    assert "�→" not in md


@pytest.fixture
def pdf_cmsy10_texto_em_volta(tmp_path: Path) -> Path:
    """Gancho LMMathItalic + seta CMSY10 com texto Helvetica antes e depois, na mesma linha."""
    caminho = tmp_path / "cmsy10_texto_em_volta.pdf"
    caminho.write_bytes(_montar_pdf("LMMathItalic10-Regular", texto_em_volta=True))
    return caminho


def test_pdf_to_md_cmsy10_isolado_troca_por_hook_linha(pdf_gancho: Path) -> None:
    """Layout CMSY10 isolado: a seta de outra fonte vira `↪` sem `�` no Markdown."""
    md = pdf_to_md(pdf_gancho)
    assert "↪" in md
    assert "�" not in md


def test_pdf_to_md_cmsy10_com_texto_em_volta_troca_por_hook_linha(
    pdf_cmsy10_texto_em_volta: Path,
) -> None:
    """Gancho no meio de frase com seta de CMSY10: sai `A ↪ B`, sem `�` e sem itálico.

    Motivo: o span itálico do gancho fecha antes da seta, que vem de outra fonte.
    Sem o reparo o Markdown sai como `A _�_ → B`, que não é `�→` e escapa do reparo antigo.
    """
    md = pdf_to_md(pdf_cmsy10_texto_em_volta)
    assert "A ↪ B" in md
    assert "�" not in md
