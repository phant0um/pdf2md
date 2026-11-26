"""
Reparo de glifos de fonte matemática TeX sem ToUnicode (U+FFFD).

Motivo: o pymupdf4llm devolve U+FFFD para glifos de fontes TeX (LMMath*, cmex,
txexs) que não trazem ToUnicode. Com a flag TEXT_CID_FOR_UNKNOWN_UNICODE, o
mesmo caractere volta como o código bruto da fonte. Pareando as duas extrações
por linha, char a char, sabemos (fonte, código) de cada U+FFFD. Pareamento é
por linha e não por span: a flag CID muda como o MuPDF divide os spans.

Regra única aceita: o glifo de gancho de quebra de linha (fonte LMMathItalic,
código 0x2C = \\lhook) é seguido de → (U+2192) e junto formam ↪. A prova é por
ocorrência: cada `�→` precisa de um gancho comprovado. Gancho comprovado é
(LMMathItalic*, 0x2C) cujo caractere seguinte na mesma linha é →; a seta pode
vir em outro span (outra fonte), por isso olhamos a linha inteira e não só o
span. Se há mais padrões `�→` que provas, não sabemos quais são gancho e nada
é trocado (desde 2026-11-26; antes bastava uma prova para trocar todos).

Três formas do mesmo gancho no Markdown, conforme as fontes dos spans:
- `�→`: gancho e seta na mesma fonte (ex.: LMMathItalic), sem separação de span;
- `_�_ →`: o gancho fica em itálico e a seta vem depois, de outra fonte (CMSY10);
- `→ _�_`: a seta vem antes do gancho na mesma linha (linha só com os dois glifos).
As três viram `↪` sem itálico, porque a seta é de fonte reta. WHY: o span itálico
do gancho fecha antes da seta de outra fonte, e por isso o `�→` contíguo nunca
aparece nesse layout; o reparo casa a forma que o Markdown de fato produz.
"""
import re
from typing import Any

import fitz

# Código do glifo \lhook na fonte LMMathItalic (verificado em PDF de origem).
_CODIGO_HOOK = 0x2C
_PREFIXO_FONTE_HOOK = "LMMathItalic"
_SETA = "→"
_HOOK_LINHA = "↪"  # ↪
# As três formas do gancho no Markdown (ver docstring do módulo). Alternância
# numa regex só: uma passada troca cada trecho no máximo uma vez.
_PADRAO_HOOK = re.compile(r"�→|_�_ →|→ _�_")

# (nome_da_fonte, código_bruto, proximo). proximo = caractere seguinte na mesma
# linha da extração normal; "" só se for fim de linha. Desconhecido não é prova:
# "" nunca vira "→" por suposição.
Glifo = tuple[str, int, str]

# Entrada de linha achatada: (char_normal, código_bruto, fonte) para cada char pareado.
_Item = tuple[str, int, str]


def extrair_glifos_tex(doc: fitz.Document) -> list[Glifo]:
    """
    Lista (fonte, código, proximo) de cada U+FFFD do documento, na ordem do texto cru.

    Pareia a extração normal (sem TEXT_CID_FOR_UNKNOWN_UNICODE) com a que traz o
    código bruto, linha a linha, por sequência de chars. Página com estrutura de
    linhas diferente é pulada; linha com contagem de chars diferente é pulada.
    Um pareamento errado trocaria símbolo por símbolo.

    O `proximo` é lido na linha inteira (spans achatados), porque em TeX o →
    costuma estar num span de outra fonte logo depois do gancho.

    Args:
        doc: Documento PyMuPDF já aberto.

    Returns:
        Lista de tuplas (nome_da_fonte, código_bruto, proximo). Vazia se não há U+FFFD.
    """
    flags_sem = fitz.TEXTFLAGS_RAWDICT & ~fitz.TEXT_CID_FOR_UNKNOWN_UNICODE
    flags_com = fitz.TEXTFLAGS_RAWDICT
    glifos: list[Glifo] = []
    for pagina in doc:
        cru = pagina.get_text("rawdict", flags=flags_sem)
        bruto = pagina.get_text("rawdict", flags=flags_com)
        linhas = _itens_por_linha(cru, bruto)
        if linhas is None:
            continue
        for itens in linhas:
            glifos.extend(_glifos_da_linha(itens))
    return glifos


def _linhas_por_bloco(pagina_dict: dict[str, Any]) -> list[list[list[dict[str, Any]]]]:
    """
    Blocos de texto (tipo 0) da página: bloco -> linhas -> spans.

    HOW: mantém a divisão por bloco (e não uma lista plana de linhas) para
    comparar a estrutura das duas vistas antes de parear qualquer char.
    """
    return [
        [linha.get("spans", []) for linha in bloco.get("lines", [])]
        for bloco in pagina_dict.get("blocks", [])
        if bloco.get("type") == 0
    ]


def _itens_por_linha(cru: dict[str, Any], bruto: dict[str, Any]) -> list[list[_Item]] | None:
    """
    Pareia as duas extrações por linha e devolve, por linha, os chars em ordem crua.

    Devolve None se a estrutura de blocos e linhas difere entre as vistas: a
    página inteira é pulada, porque sem mesma estrutura não há alinhamento.
    Linha com contagem de chars diferente sai vazia; as outras seguem pareadas.
    """
    blocos_cru = _linhas_por_bloco(cru)
    blocos_bruto = _linhas_por_bloco(bruto)
    if [len(b) for b in blocos_cru] != [len(b) for b in blocos_bruto]:
        return None

    resultado: list[list[_Item]] = []
    for bloco_cru, bloco_bruto in zip(blocos_cru, blocos_bruto, strict=True):
        for linha_cru, linha_bruto in zip(bloco_cru, bloco_bruto, strict=True):
            resultado.append(_chars_pareados(linha_cru, linha_bruto))
    return resultado


def _chars_pareados(linha_cru: list[dict[str, Any]], linha_bruto: list[dict[str, Any]]) -> list[_Item]:
    """
    Achata os chars de uma linha nas duas vistas e os pareia um a um.

    HOW: o MuPDF divide spans de forma diferente quando a flag CID muda o texto
    de um glifo (ex.: `�→` num span só sem a flag, em dois spans com a flag).
    Por isso não pareamos span com span: o que as duas vistas têm em comum é a
    sequência de chars da linha. Fonte vem da vista com a flag (código bruto).
    Se as sequências têm tamanhos diferentes, a linha inteira fica de fora,
    porque não sabemos qual char de uma vista corresponde a qual da outra.
    """
    chars_cru = [c["c"] for span in linha_cru for c in span.get("chars", [])]
    chars_bruto = [
        (c["c"], span.get("font", ""))
        for span in linha_bruto
        for c in span.get("chars", [])
    ]
    if len(chars_cru) != len(chars_bruto):
        return []
    return [
        (char_cru, ord(char_bruto), fonte)
        for char_cru, (char_bruto, fonte) in zip(chars_cru, chars_bruto, strict=True)
    ]


def _glifos_da_linha(itens: list[_Item]) -> list[Glifo]:
    """
    Gera (fonte, código, proximo) para cada U+FFFD pareado da linha.

    HOW: o próximo caractere é o item i+1 da mesma linha. Se i+1 não existe
    (fim de linha), proximo fica "": o que vem depois não é prova.
    """
    glifos: list[Glifo] = []
    for i, (char, codigo, fonte) in enumerate(itens):
        if char != "�":
            continue
        proximo = itens[i + 1][0] if i + 1 < len(itens) else ""
        glifos.append((fonte, codigo, proximo))
    return glifos


def tem_glifo_hook(glifos: list[Glifo]) -> bool:
    """
    Indica se o documento contém o glifo de gancho de quebra de linha.

    Args:
        glifos: Saída de extrair_glifos_tex.

    Returns:
        True se há ao menos um gancho seguido de → na mesma linha.
    """
    return _conta_hooks(glifos) > 0


def _conta_hooks(glifos: list[Glifo]) -> int:
    """
    Conta as ocorrências de (LMMathItalic*, 0x2C, →) — uma por prova de gancho.

    Contar (e não só detectar) é o que permite comparar com o número de `�→`.
    O → exigido é o que separa gancho de outro glifo LMMathItalic com código
    0x2C: sem a seta na linha, o U+FFFD não é prova de `�→`.
    """
    return sum(
        1
        for fonte, codigo, proximo in glifos
        if fonte.startswith(_PREFIXO_FONTE_HOOK) and codigo == _CODIGO_HOOK and proximo == _SETA
    )


def reparar_hook_linha(md: str, glifos: list[Glifo]) -> tuple[str, int]:
    """
    Troca as três formas do gancho (`�→`, `_�_ →`, `→ _�_`) por ↪ só quando
    há uma prova de gancho para cada ocorrência.

    Prova por documento não basta: um único \\lhook faria todo padrão virar ↪,
    inclusive os de outras fontes. Então comparamos contagens, somando as três
    formas:
    - sem padrão ou sem gancho comprovado: texto intacto, 0;
    - padrões <= provas: todos viram ↪, devolve o número de padrões;
    - padrões > provas: nenhum é trocado, devolve (md, 0). Não dá para saber
      quais são gancho, e a regra do módulo é "sem prova, nada é trocado".

    HOW: uma única passada com alternância (re.sub) em vez de três str.replace
    em sequência. Assim cada trecho do Markdown é trocado no máximo uma vez, e a
    contagem é a das trocas reais, não a soma de contagens que podem se sobrepor.

    Args:
        md: Markdown com possíveis U+FFFD.
        glifos: Saída de extrair_glifos_tex sobre o mesmo documento.

    Returns:
        (md_reparado, n_substituicoes)
    """
    n_hook = _conta_hooks(glifos)
    if n_hook == 0:
        return md, 0
    n_padrao = len(_PADRAO_HOOK.findall(md))
    if n_padrao == 0 or n_padrao > n_hook:
        return md, 0
    return _PADRAO_HOOK.sub(_HOOK_LINHA, md), n_padrao
