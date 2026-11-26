"""
Testes para core/glifos_tex.py — reparo do gancho TeX (U+FFFD + →).
"""

import fitz

from core.glifos_tex import extrair_glifos_tex, reparar_hook_linha, tem_glifo_hook

_HOOK = ("LMMathItalic6-Regular", 0x2C)
_SOMA = ("LMMathExtension10-Regular", 0x58)


def test_tem_glifo_hook_detecta_fonte_e_codigo():
    """Só (LMMathItalic*, 0x2C) conta como gancho comprovado."""
    assert tem_glifo_hook([_HOOK])


def test_tem_glifo_hook_recusa_codigo_diferente():
    """Mesma fonte com outro código não é gancho."""
    assert not tem_glifo_hook([("LMMathItalic6-Regular", 0x2D)])


def test_tem_glifo_hook_recusa_fonte_diferente():
    """Mesmo código em fonte que não é LMMathItalic não é gancho."""
    assert not tem_glifo_hook([("txexs", 0x2C)])


def test_reparar_hook_substitui_fffd_seta():
    """`�→` vira ↪ quando o documento prova o gancho."""
    md, n = reparar_hook_linha("- _�→_ texto", [_HOOK])
    assert md == "- _↪_ texto"
    assert n == 1


def test_reparar_hook_seta_dupla():
    """`�→→` vira `↪→`: só o U+FFFD é trocado, a seta seguinte fica."""
    md, n = reparar_hook_linha("�→→", [_HOOK])
    assert md == "↪→"
    assert n == 1


def test_reparar_hook_sem_prova_mantem_texto():
    """Sem o gancho no documento, `�→` fica intacto."""
    original = "a�→b"
    md, n = reparar_hook_linha(original, [_SOMA])
    assert md == original
    assert n == 0


def test_reparar_hook_codigo_desconhecido_fica_fffd():
    """U+FFFD sem seta ao lado não é trocado por nenhum símbolo."""
    md, n = reparar_hook_linha("x� y", [_HOOK])
    assert md == "x� y"
    assert n == 0


def test_reparar_hook_provas_cobrem_padroes_troca_todos():
    """Uma prova por `�→` (n_padrao <= n_hook): todos viram ↪."""
    md, n = reparar_hook_linha("�→ x �→ y", [_HOOK, _HOOK])
    assert md == "↪ x ↪ y"
    assert n == 2


def test_reparar_hook_mais_padroes_que_provas_nao_troca_nenhum():
    """Uma prova para dois `�→` (n_padrao > n_hook): nenhum é trocado.

    Motivo: com uma prova só não dá para saber qual ocorrência é o gancho;
    trocar todas repetiria o erro de confiar no documento inteiro.
    """
    original = "�→ x �→ y"
    md, n = reparar_hook_linha(original, [_HOOK])
    assert md == original
    assert n == 0


# --- Finding B: extrair_glifos_tex com documento fake (duck typing) ---


class _FakePagina:
    """Página fake: get_text devolve o dict rawdict conforme a flag."""

    def __init__(self, sem_cid: dict, com_cid: dict) -> None:
        self._sem = sem_cid
        self._com = com_cid

    def get_text(self, modo: str, flags: int = 0) -> dict:
        assert modo == "rawdict"
        if flags & fitz.TEXT_CID_FOR_UNKNOWN_UNICODE:
            return self._com
        return self._sem


def _span(font: str, chars: list[str]) -> dict:
    return {"font": font, "chars": [{"c": c} for c in chars]}


def _pagina_com_spans(spans_sem: list[dict], spans_com: list[dict]) -> _FakePagina:
    """Monta página fake com um bloco de texto (type 0) por lista de spans."""
    sem = {"blocks": [{"type": 0, "lines": [{"spans": spans_sem}]}]}
    com = {"blocks": [{"type": 0, "lines": [{"spans": spans_com}]}]}
    return _FakePagina(sem, com)


def test_extrair_glifos_pareia_fffd_com_codigo_bruto():
    """U+FFFD na extração normal + código bruto na com-flag vira (fonte, código)."""
    pagina = _pagina_com_spans(
        [_span("LMMathItalic6-Regular", ["�"])],
        [_span("LMMathItalic6-Regular", [","])],
    )
    assert extrair_glifos_tex([pagina]) == [("LMMathItalic6-Regular", 0x2C)]


def test_extrair_glifos_pula_pagina_com_numero_de_spans_diferente():
    """Spans em contagens diferentes: a página inteira é pulada."""
    pagina = _pagina_com_spans(
        [_span("txexs", ["�"]), _span("txexs", ["a"])],
        [_span("txexs", [","])],
    )
    assert extrair_glifos_tex([pagina]) == []


def test_extrair_glifos_pula_span_com_contagem_de_chars_diferente():
    """Char count divergente pula só aquele span; os outros continuam pareados."""
    pagina = _pagina_com_spans(
        [
            _span("txexs", ["�", "a"]),
            _span("LMMathItalic6-Regular", ["�"]),
        ],
        [
            _span("txexs", [","]),
            _span("LMMathItalic6-Regular", [","]),
        ],
    )
    assert extrair_glifos_tex([pagina]) == [("LMMathItalic6-Regular", 0x2C)]


def test_extrair_glifos_ignora_blocos_que_nao_sao_texto():
    """Bloco de imagem (type 1) não entra na conta, mesmo com U+FFFD dentro."""
    sem = {
        "blocks": [
            {"type": 1, "lines": [{"spans": [_span("txexs", ["�"])]}]},
        ]
    }
    com = {
        "blocks": [
            {"type": 1, "lines": [{"spans": [_span("txexs", [","])]}]},
        ]
    }
    assert extrair_glifos_tex([_FakePagina(sem, com)]) == []


def test_extrair_glifos_sem_fffd_devolve_lista_vazia():
    """Página sem U+FFFD não produz glifos."""
    pagina = _pagina_com_spans(
        [_span("txexs", ["a", "b"])],
        [_span("txexs", ["a", "b"])],
    )
    assert extrair_glifos_tex([pagina]) == []
