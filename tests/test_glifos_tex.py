"""
Testes para core/glifos_tex.py — reparo do gancho TeX (U+FFFD + →).
"""

import fitz

from core.glifos_tex import extrair_glifos_tex, reparar_hook_linha, tem_glifo_hook

# Cada glifo carrega (fonte, código, proximo): proximo é o caractere seguinte
# na mesma linha da extração normal. Gancho só vale se proximo for "→".
_HOOK = ("LMMathItalic6-Regular", 0x2C, "→")
_HOOK_SEM_SETA = ("LMMathItalic6-Regular", 0x2C, "")
_SOMA = ("LMMathExtension10-Regular", 0x58, "")


def test_tem_glifo_hook_detecta_fonte_e_codigo():
    """Só (LMMathItalic*, 0x2C, →) conta como gancho comprovado."""
    assert tem_glifo_hook([_HOOK])


def test_tem_glifo_hook_recusa_codigo_diferente():
    """Mesma fonte com outro código não é gancho."""
    assert not tem_glifo_hook([("LMMathItalic6-Regular", 0x2D, "→")])


def test_tem_glifo_hook_recusa_fonte_diferente():
    """Mesmo código em fonte que não é LMMathItalic não é gancho."""
    assert not tem_glifo_hook([("txexs", 0x2C, "→")])


def test_tem_glifo_hook_recusa_gancho_sem_seta():
    """Gancho sem → logo depois não é prova: pode ser outro símbolo de fim de linha."""
    assert not tem_glifo_hook([_HOOK_SEM_SETA])


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


def test_reparar_tres_ganchos_so_dois_com_seta_nao_troca_os_tres():
    """Três ganchos, só dois seguidos de →, e um `�→` de outra fonte: não troca.

    Motivo: contar ganchos sem olhar o caractere seguinte deixaria 3 provas
    cobrir os 3 `�→`, inclusive o de fonte que não é gancho. Com 2 provas
    válidas para 3 padrões, nenhum é trocado.
    """
    original = "�→ a �→ b �→ c"
    glifos = [_HOOK, _HOOK, _HOOK_SEM_SETA]
    md, n = reparar_hook_linha(original, glifos)
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


def _pagina_com_linhas(linhas_sem: list[list[dict]], linhas_com: list[list[dict]]) -> _FakePagina:
    """Monta página fake com um bloco de texto (type 0) por linha de spans."""
    sem = {"blocks": [{"type": 0, "lines": [{"spans": s} for s in linhas_sem]}]}
    com = {"blocks": [{"type": 0, "lines": [{"spans": s} for s in linhas_com]}]}
    return _FakePagina(sem, com)


def _pagina_com_spans(spans_sem: list[dict], spans_com: list[dict]) -> _FakePagina:
    """Atalho: todos os spans numa linha só."""
    return _pagina_com_linhas([spans_sem], [spans_com])


def test_extrair_glifos_pareia_fffd_com_codigo_bruto():
    """U+FFFD na extração normal + código bruto na com-flag vira (fonte, código, proximo).

    Sem caractere depois do U+FFFD na linha, proximo é "".
    """
    pagina = _pagina_com_spans(
        [_span("LMMathItalic6-Regular", ["�"])],
        [_span("LMMathItalic6-Regular", [","])],
    )
    assert extrair_glifos_tex([pagina]) == [("LMMathItalic6-Regular", 0x2C, "")]


def test_extrair_glifos_pula_pagina_com_numero_de_linhas_diferente():
    """Contagem de linhas diferente entre as vistas: a página inteira é pulada.

    Motivo: sem a mesma estrutura de linhas não há como alinhar as duas
    extrações; a regra nova (pareamento por linha) só vale com linhas iguais.
    """
    pagina = _pagina_com_linhas(
        [[_span("txexs", ["�"])], [_span("txexs", ["a"])]],
        [[_span("txexs", [","])]],
    )
    assert extrair_glifos_tex([pagina]) == []


def test_extrair_glifos_linha_com_chars_diferentes_pula_so_a_linha():
    """Contagem de chars divergente numa linha pula essa linha; as outras seguem pareadas.

    Motivo: antes a regra era por span; agora é por linha. Um desalinhamento
    numa linha não contamina as demais da página.
    """
    pagina = _pagina_com_linhas(
        [
            [_span("txexs", ["�", "a"])],
            [_span("LMMathItalic6-Regular", ["�"])],
        ],
        [
            [_span("txexs", [","])],
            [_span("LMMathItalic6-Regular", [","])],
        ],
    )
    assert extrair_glifos_tex([pagina]) == [("LMMathItalic6-Regular", 0x2C, "")]


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


def test_extrair_proximo_seta_no_span_seguinte_da_mesma_linha():
    """Seta num span de OUTRA fonte na mesma linha ainda é o proximo do gancho.

    Motivo: em TeX a seta costuma vir em outra fonte logo depois do gancho.
    """
    pagina = _pagina_com_linhas(
        [[_span("LMMathItalic6-Regular", ["�"]), _span("cmr10", ["→"])]],
        [[_span("LMMathItalic6-Regular", [","]), _span("cmr10", ["→"])]],
    )
    assert extrair_glifos_tex([pagina]) == [("LMMathItalic6-Regular", 0x2C, "→")]


def test_extrair_proximo_vazio_quando_seta_esta_na_linha_seguinte():
    """Seta só na linha de baixo não conta: fim de linha é fronteira, proximo é ""."""
    pagina = _pagina_com_linhas(
        [[_span("LMMathItalic6-Regular", ["�"])], [_span("cmr10", ["→"])]],
        [[_span("LMMathItalic6-Regular", [","])], [_span("cmr10", ["→"])]],
    )
    assert extrair_glifos_tex([pagina]) == [("LMMathItalic6-Regular", 0x2C, "")]


def test_extrair_linha_com_chars_divergentes_nao_gera_glifo():
    """Linha com contagem de chars diferente não gera glifo, nem o gancho dela.

    Motivo: a regra mudou de span para linha. Antes o span seguinte divergente
    virava proximo ""; agora a linha inteira fica sem pareamento, porque não
    há como saber qual char de uma vista corresponde a qual da outra.
    """
    pagina = _pagina_com_linhas(
        [[_span("LMMathItalic6-Regular", ["�"]), _span("cmr10", ["→", "b"])]],
        [[_span("LMMathItalic6-Regular", [","]), _span("cmr10", [","])]],
    )
    assert extrair_glifos_tex([pagina]) == []


def test_extrair_mesma_linha_com_split_diferente_entre_vistas_pareia():
    """Mesma linha em 1 span (sem flag) e 2 spans (com flag): pareia por char, proximo "→".

    Motivo: o MuPDF divide spans de forma diferente quando a flag CID muda o
    texto de um glifo. O que as duas vistas têm em comum são os chars, não os spans.
    """
    pagina = _pagina_com_linhas(
        [[_span("LMMathItalic6-Regular", ["�", "→"])]],
        [[_span("LMMathItalic6-Regular", [","]), _span("cmr10", ["→"])]],
    )
    assert extrair_glifos_tex([pagina]) == [("LMMathItalic6-Regular", 0x2C, "→")]


# --- Formas `_�_ →` e `→ _�_`: gancho e seta em fontes diferentes ---


def test_reparar_hook_forma_italico_seta_depois_com_prova_troca():
    """`_�_ →` (gancho em itálico, seta de outra fonte) vira ↪ quando há prova."""
    md, n = reparar_hook_linha("Seja f: A _�_ → B uma inclusao.", [_HOOK])
    assert md == "Seja f: A ↪ B uma inclusao."
    assert n == 1


def test_reparar_hook_forma_seta_antes_com_prova_troca():
    """`→ _�_` (seta antes do gancho, linha só com os dois glifos) vira ↪ com prova."""
    md, n = reparar_hook_linha("→ _�_ ", [_HOOK])
    assert md == "↪ "
    assert n == 1


def test_reparar_hook_forma_nova_sem_prova_nao_troca():
    """`_�_ →` sem gancho comprovado fica intacto: sem prova, nada é trocado."""
    original = "A _�_ → B"
    md, n = reparar_hook_linha(original, [_SOMA])
    assert md == original
    assert n == 0


def test_reparar_hook_forma_nova_com_gancho_sem_seta_nao_troca():
    """Gancho sem → na mesma linha não prova `_�_ →` nem `→ _�_`."""
    original = "→ _�_ e A _�_ → B"
    md, n = reparar_hook_linha(original, [_HOOK_SEM_SETA])
    assert md == original
    assert n == 0


def test_reparar_hook_mistura_mais_padroes_que_provas_nao_troca_nenhum():
    """Negativo plantado: três formas juntas, duas provas. Nenhum é trocado.

    Motivo: somar as três formas tem de contar, senão a forma nova escaparia
    da regra "mais padrões que provas" e trocaria um padrão sem prova.
    """
    original = "�→ a _�_ → b → _�_ c"
    md, n = reparar_hook_linha(original, [_HOOK, _HOOK])
    assert md == original
    assert n == 0


def test_reparar_hook_mistura_com_provas_suficientes_troca_todas_as_formas():
    """Três formas e três provas: todas viram ↪, e o total é 3."""
    md, n = reparar_hook_linha("�→ a _�_ → b → _�_ c", [_HOOK, _HOOK, _HOOK])
    assert md == "↪ a ↪ b ↪ c"
    assert n == 3
