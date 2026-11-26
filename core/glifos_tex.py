"""
Reparo de glifos de fonte matemática TeX sem ToUnicode (U+FFFD).

Motivo: o pymupdf4llm devolve U+FFFD para glifos de fontes TeX (LMMath*, cmex,
txexs) que não trazem ToUnicode. Com a flag TEXT_CID_FOR_UNKNOWN_UNICODE, o
mesmo caractere volta como o código bruto da fonte. Pareando as duas extrações
por span (mesmo número de chars) sabemos (fonte, código) de cada U+FFFD.

Regra única aceita: o glifo de gancho de quebra de linha (fonte LMMathItalic,
código 0x2C = \\lhook) sempre vem antes de → (U+2192) e junto formam ↪. Só
substituímos `�→` quando o próprio documento contém esse glifo — sem prova
no documento, nada é trocado.
"""
from typing import Any

import fitz

# Código do glifo \lhook na fonte LMMathItalic (verificado em PDF de origem).
_CODIGO_HOOK = 0x2C
_PREFIXO_FONTE_HOOK = "LMMathItalic"

_SETA = "→"
_HOOK_LINHA = "↪"  # ↪


def extrair_glifos_tex(doc: fitz.Document) -> list[tuple[str, int]]:
    """
    Lista (fonte, código) de cada U+FFFD do documento, na ordem do texto cru.

    Pareia a extração normal (sem TEXT_CID_FOR_UNKNOWN_UNICODE) com a que traz o
    código bruto. Só pareia spans com o mesmo número de chars; span divergente
    é pulado — um pareamento errado trocaria símbolo por símbolo.

    Args:
        doc: Documento PyMuPDF já aberto.

    Returns:
        Lista de tuplas (nome_da_fonte, código_bruto). Vazia se não há U+FFFD.
    """
    flags_sem = fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_CID_FOR_UNKNOWN_UNICODE
    flags_com = fitz.TEXTFLAGS_RAWDICT
    glifos: list[tuple[str, int]] = []
    for pagina in doc:
        cru = pagina.get_text("rawdict", flags=flags_sem)
        bruto = pagina.get_text("rawdict", flags=flags_com)
        spans_cru = _spans_texto(cru)
        spans_bruto = _spans_texto(bruto)
        if len(spans_cru) != len(spans_bruto):
            continue
        for sc, sb in zip(spans_cru, spans_bruto, strict=True):
            chars_cru = sc.get("chars", [])
            chars_bruto = sb.get("chars", [])
            if len(chars_cru) != len(chars_bruto):
                continue
            for c_cru, c_bruto in zip(chars_cru, chars_bruto, strict=True):
                if c_cru["c"] == "�":
                    glifos.append((sb["font"], ord(c_bruto["c"])))
    return glifos


def _spans_texto(pagina_dict: dict[str, Any]) -> list[dict[str, Any]]:
    """Achata os spans de texto (bloco tipo 0) de um dict rawdict."""
    return [
        span
        for bloco in pagina_dict.get("blocks", [])
        if bloco.get("type") == 0
        for linha in bloco.get("lines", [])
        for span in linha.get("spans", [])
    ]


def tem_glifo_hook(glifos: list[tuple[str, int]]) -> bool:
    """
    Indica se o documento contém o glifo de gancho de quebra de linha.

    Args:
        glifos: Saída de extrair_glifos_tex.

    Returns:
        True se há ao menos um (LMMathItalic*, 0x2C).
    """
    return any(
        fonte.startswith(_PREFIXO_FONTE_HOOK) and codigo == _CODIGO_HOOK
        for fonte, codigo in glifos
    )


def reparar_hook_linha(md: str, glifos: list[tuple[str, int]]) -> tuple[str, int]:
    """
    Troca `�→` por ↪ quando o documento tem o glifo de gancho comprovado.

    Sem o glifo comprovado no documento, devolve o texto intacto — o U+FFFD
    seguido de seta pode ter outra origem e não é trocado às cegas.

    Args:
        md: Markdown com possíveis U+FFFD.
        glifos: Saída de extrair_glifos_tex sobre o mesmo documento.

    Returns:
        (md_reparado, n_substituicoes)
    """
    if not tem_glifo_hook(glifos):
        return md, 0
    padrao = "�" + _SETA
    n = md.count(padrao)
    return md.replace(padrao, _HOOK_LINHA), n
