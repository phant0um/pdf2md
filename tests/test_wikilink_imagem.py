"""Testes para core/image_converter.wikilink_imagem (alias de embed Obsidian)."""
import pytest

from core.image_converter import wikilink_imagem


@pytest.mark.parametrize("alt", [None, "", "imagem", "300", "640x480"])
def test_sem_alias_quando_vazio_default_ou_tamanho(alt):
    """None, vazio, default e tamanho (`300`, `640x480`) não viram alias."""
    assert wikilink_imagem("n.png", alt) == "![[n.png]]"


def test_alias_limpa_caracteres_que_quebram_wikilink():
    """`|`, `[` e `]` viram espaço; espaços colapsam."""
    assert wikilink_imagem("n", "a|b]]c [d]") == "![[n|a b c d]]"


def test_alias_nunca_contem_delimitadores():
    """Qualquer alt, por mais hostil, sai sem `|`, `[` ou `]` no alias."""
    saida = wikilink_imagem("n.png", "x | [y] ]] |z")
    alias = saida[len("![[n.png|"):-len("]]")]
    assert not any(c in alias for c in "|[]")


def test_alias_so_delimitadores_vira_sem_alias():
    """Alt que só tem delimitadores fica vazio após a limpeza → sem alias."""
    assert wikilink_imagem("n.png", " | [] ") == "![[n.png]]"


def test_alias_texto_normal():
    """Texto comum vira alias literal."""
    assert wikilink_imagem("n.png", "Gráfico de vendas") == "![[n.png|Gráfico de vendas]]"
