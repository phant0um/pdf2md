"""
Testes para core/glifos_tex.py — reparo do gancho TeX (U+FFFD + →).
"""

from core.glifos_tex import reparar_hook_linha, tem_glifo_hook

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
