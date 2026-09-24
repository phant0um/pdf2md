"""
Testes para core/quality.py — limpeza de artefatos e validação de qualidade.
"""


from core.quality import (
    aplicar_pipeline_qualidade,
    corrigir_mojibake,
    limpar_artefatos,
    reparar_ligaduras,
    validar_qualidade,
)

# ── limpar_artefatos ─────────────────────────────────────────────────────────

def test_remove_soft_hyphen():
    """Hifens suaves (U+00AD) são removidos."""
    texto = "pala­vra"
    assert limpar_artefatos(texto) == "palavra"


def test_remove_soft_hyphen_multiplos():
    """Vários hifens suaves em sequência são todos removidos."""
    texto = "con­ver­são"
    assert limpar_artefatos(texto) == "conversão"


def test_remove_zero_width_space():
    """U+200B (zero-width space) é removido."""
    texto = "le​tra"
    assert limpar_artefatos(texto) == "letra"


def test_remove_zero_width_non_joiner():
    """U+200C (zero-width non-joiner) é removido."""
    texto = "le‌tra"
    assert limpar_artefatos(texto) == "letra"


def test_remove_zero_width_joiner():
    """U+200D (zero-width joiner) é removido."""
    texto = "le‍tra"
    assert limpar_artefatos(texto) == "letra"


def test_remove_bom_mid_string():
    """U+FEFF mid-string é removido."""
    texto = "início﻿meio"
    assert limpar_artefatos(texto) == "iníciomeio"


def test_normaliza_nbsp():
    """U+00A0 (non-breaking space) vira espaço ASCII."""
    texto = "palavra seguinte"
    assert limpar_artefatos(texto) == "palavra seguinte"


def test_preserva_texto_normal():
    """Texto limpo não é alterado."""
    texto = "Texto normal com acentos: ção, ã, é, ó."
    assert limpar_artefatos(texto) == texto


def test_preserva_quebras_de_linha():
    """Quebras de linha reais são preservadas."""
    texto = "linha1\nlinha2\n\nlinha4"
    resultado = limpar_artefatos(texto)
    assert "linha1" in resultado
    assert "linha2" in resultado
    assert "linha4" in resultado


def test_colapsa_linha_so_whitespace():
    """Linha que ficou só com espaços é colapsada para linha vazia."""
    texto = "linha1\n   \nlinha3"
    resultado = limpar_artefatos(texto)
    linhas = resultado.split("\n")
    assert linhas[1] == ""


# ── corrigir_mojibake ────────────────────────────────────────────────────────

def test_corrige_ã():
    """Ã£ → ã (padrão mais comum em PT-BR)."""
    texto, n = corrigir_mojibake("conversiÃ£o")
    assert "ã" in texto
    assert n > 0


def test_corrige_ç():
    """Ã§ → ç."""
    texto, n = corrigir_mojibake("Ã§")
    assert texto == "ç"
    assert n == 1


def test_corrige_é():
    """Ã© → é."""
    texto, n = corrigir_mojibake("caf Ã©")
    assert "é" in texto


def test_corrige_multiplos():
    """Múltiplos padrões corrompidos em um texto."""
    corrompido = "integraÃ§Ã£o"  # "integração"
    correto, n = corrigir_mojibake(corrompido)
    assert "ç" in correto
    assert "ã" in correto
    assert n >= 2


def test_sem_mojibake_retorna_inalterado():
    """Texto sem mojibake retorna sem modificação e n=0."""
    texto = "Texto correto com ação e coração."
    resultado, n = corrigir_mojibake(texto)
    assert resultado == texto
    assert n == 0


# ── validar_qualidade ────────────────────────────────────────────────────────

def test_sem_avisos_texto_limpo(tmp_path):
    """Texto limpo não gera avisos."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF" + b"x" * 5000)  # 5KB falso
    md = "# Título\n\nConteúdo correto com acentos: ação, coração.\n" * 20
    avisos = validar_qualidade(md, f)
    assert avisos == []


def test_detecta_fffd(tmp_path):
    """U+FFFD (char de substituição) gera aviso."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF" + b"x" * 1000)
    md = "texto com caracter� corrompido"
    avisos = validar_qualidade(md, f)
    assert any("FFFD" in a or "substituição" in a.lower() for a in avisos)


def _aviso_fffd(md, tmp_path):
    """Helper: avisos de validar_qualidade que mencionam U+FFFD/símbolo."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF" + b"x" * 1000)
    return validar_qualidade(md, f)


def test_fffd_formula_somatorio_nao_e_corrupcao(tmp_path):
    """Σ sem ToUnicode (arXiv 2502.02533) vira aviso de símbolo, não de encoding."""
    md = "custo _𝐶_ (1PO) =[\ufffd] _[𝐽] 𝑗_[N (] _[𝑎][𝑗]_[) ×]"
    avisos = _aviso_fffd(md, tmp_path)
    assert len(avisos) == 1
    assert "1 símbolo(s)" in avisos[0]
    assert "corrompido" not in avisos[0]


def test_fffd_isolado_em_legenda_nao_e_corrupcao(tmp_path):
    """'=' de fonte pxfonts em legenda de gráfico (arXiv 2608.11246)."""
    md = "Open loop<br>0.2 α α \ufffd \ufffd 1 0 . . 0 95<br>α \ufffd 0.9"
    avisos = _aviso_fffd(md, tmp_path)
    assert len(avisos) == 1
    assert "3 símbolo(s)" in avisos[0]
    assert "corrompido" not in avisos[0]


def test_fffd_colado_em_letra_matematica_e_simbolo(tmp_path):
    """Vizinho letra matemática/grega (𝑗, α) não conta como palavra de texto."""
    md = "soma 𝑗\ufffd𝐽 e α\ufffdβ"
    avisos = _aviso_fffd(md, tmp_path)
    assert len(avisos) == 1
    assert "2 símbolo(s)" in avisos[0]


def test_fffd_dentro_de_palavra_continua_corrupcao(tmp_path):
    """Ligadura não reparada (Work\ufffdows) segue como encoding corrompido."""
    md = "multi-agent Work\ufffdows and caracter\ufffd"
    avisos = _aviso_fffd(md, tmp_path)
    assert len(avisos) == 1
    assert "2 caractere(s) de substituição" in avisos[0]
    assert "corrompido" in avisos[0]


def test_fffd_sequencia_dentro_de_palavra_conta_todos(tmp_path):
    """Run de U+FFFD colado em palavra conta cada caractere, inclusive o do meio."""
    md = "a\ufffd\ufffd\ufffdb"
    avisos = _aviso_fffd(md, tmp_path)
    assert avisos == [a for a in avisos if "3 caractere(s)" in a]
    assert len(avisos) == 1


def test_fffd_misto_gera_dois_avisos(tmp_path):
    """Palavra corrompida e símbolo de fórmula no mesmo doc: 2 avisos separados."""
    md = "re\ufffdect aqui e =[\ufffd] _𝑛 ali"
    avisos = _aviso_fffd(md, tmp_path)
    assert len(avisos) == 2
    assert any("1 caractere(s)" in a and "corrompido" in a for a in avisos)
    assert any("1 símbolo(s)" in a for a in avisos)


def test_detecta_output_muito_curto(tmp_path):
    """Output muito curto para arquivo grande gera aviso."""
    f = tmp_path / "grande.pdf"
    f.write_bytes(b"%PDF" + b"x" * 50000)  # 50KB
    md = "curto"  # só 5 chars úteis
    avisos = validar_qualidade(md, f)
    assert any("curto" in a.lower() or "extração" in a.lower() for a in avisos)


def test_nao_avisa_output_curto_arquivo_pequeno(tmp_path):
    """Arquivo pequeno com output curto não gera aviso de output curto."""
    f = tmp_path / "pequeno.pdf"
    f.write_bytes(b"%PDF" + b"x" * 100)  # <1KB
    md = "# Título"
    avisos = validar_qualidade(md, f)
    # Não deve ter aviso de output curto para arquivo pequeno
    assert not any("curto" in a.lower() for a in avisos)


def test_texto_vazio_retorna_sem_avisos(tmp_path):
    """Texto vazio não gera avisos (é ERRO, não aviso de qualidade)."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF" + b"x" * 5000)
    avisos = validar_qualidade("", f)
    assert avisos == []


def test_detecta_soft_hyphens_residuais(tmp_path):
    """Muitos hifens suaves residuais geram aviso."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF" + b"x" * 1000)
    # 10 soft hyphens — acima do threshold de 5
    md = ("palavra­ " * 10) + "texto normal"
    avisos = validar_qualidade(md, f)
    assert any("hifen" in a.lower() or "00AD" in a or "suave" in a.lower() for a in avisos)


# ── reparar_ligaduras ───────────────────────────────────────────────────────

def test_repara_com_dicionario():
    """Repara 'fl' quando a palavra resultante existe no dicionário."""
    resultado, n = reparar_ligaduras("Work\uFFFDow", "Workflow data")
    assert resultado == "Workflow"
    assert n == 1


def test_repara_ligadura_fi():
    """Repara 'fi' (classi\u200bcation → classification)."""
    resultado, n = reparar_ligaduras("classi\uFFFDcation", "classification")
    assert resultado == "classification"
    assert n == 1


def test_repara_ligadura_ff():
    """Repara 'ff' (di\u200bfferent → different)."""
    resultado, n = reparar_ligaduras("di\uFFFDerent", "different approach")
    assert resultado == "different"
    assert n == 1


def test_nao_trunca_plural():
    """P0: plural não é truncado — Work\uFFFDows sem 'workflow' na ref fica intacto."""
    resultado, n = reparar_ligaduras("Work\uFFFDows", "workflow")
    assert resultado == "Work\uFFFDows"
    assert n == 0


def test_nao_trunca_flexao():
    """P0: flexão não é truncada — re\uFFFDected sem 'reflected' na ref fica intacto."""
    resultado, n = reparar_ligaduras("re\uFFFDected", "only reflect here")
    assert resultado == "re\uFFFDected"
    assert n == 0


def test_repara_flexao_quando_ref_tem():
    """Repara flexão quando a ref tem a palavra exata."""
    resultado, n = reparar_ligaduras("re\uFFFDected", "reflect and reflected")
    assert resultado == "reflected"
    assert n == 1


def test_preserva_sem_match():
    """Mantém U+FFFD quando nenhum candidato existe na referência."""
    resultado, n = reparar_ligaduras("Xyz\uFFFDabc", "nada a ver")
    assert resultado == "Xyz\uFFFDabc"
    assert n == 0


def test_nao_toca_token_curto():
    """Token com menos de 2 letras reais não é reparado (ex: [\ufffd])."""
    resultado, n = reparar_ligaduras("[\uFFFD]", "fl fi texto qualquer")
    assert resultado == "[\uFFFD]"
    assert n == 0


def test_noop_sem_fffd():
    """Texto sem U+FFFD retorna inalterado."""
    resultado, n = reparar_ligaduras("texto limpo", "referência")
    assert resultado == "texto limpo"
    assert n == 0


def test_noop_referencia_vazia():
    """Referência vazia: retorna inalterado."""
    resultado, n = reparar_ligaduras("Work\uFFFDow", "")
    assert resultado == "Work\uFFFDow"
    assert n == 0


def test_caixa_alta():
    """Token todo maiúsculo recebe ligadura em maiúscula."""
    resultado, n = reparar_ligaduras("WORK\uFFFDOW", "workflow")
    assert resultado == "WORKFLOW"
    assert n == 1


def test_caixa_mista_preservada():
    """Caixa mista: Re\uFFFDect → Reflect (primeira maiúscula preservada)."""
    resultado, n = reparar_ligaduras("Re\uFFFDect", "reflect")
    assert resultado == "Reflect"
    assert n == 1


def test_acento_preservado():
    """Acento PT-BR no token não é corrompido pelo reparo."""
    resultado, n = reparar_ligaduras("Work\uFFFDow e ação", "Workflow e ação")
    assert "ação" in resultado
    assert "Workflow" in resultado
    assert n == 1


def test_multiplos_tokens():
    """Múltiplos tokens com U+FFFD no mesmo texto: repara todos."""
    resultado, n = reparar_ligaduras("Work\uFFFDow e re\uFFFDect", "Workflow reflect")
    assert "Workflow" in resultado
    assert "reflect" in resultado
    assert n == 2


# ── Integração: pipeline completo ────────────────────────────────────────────

def test_pipeline_completo_texto_corrompido(tmp_path):
    """
    Texto com soft hyphen + mojibake:
    - limpar_artefatos remove soft hyphen
    - corrigir_mojibake corrige mojibake
    - validar_qualidade não detecta mais os artefatos limpos
    """
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF" + b"x" * 5000)

    texto = "pala­vra com integraÃ§Ã£o"
    limpo = limpar_artefatos(texto)
    corrigido, n = corrigir_mojibake(limpo)
    avisos = validar_qualidade(corrigido, f)

    assert "palavra" in corrigido
    assert "integração" in corrigido
    assert n >= 2
    assert avisos == []  # depois da limpeza não há mais artefatos detectáveis


# ── aplicar_pipeline_qualidade ──────────────────────────────────────────────

def test_aplicar_pipeline_limpa_e_valida(tmp_path):
    """Pipeline completo: mojibake + artefatos → limpo + avisos vazios."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF" + b"x" * 5000)
    # "integração" em mojibake = "integraÃ§Ã£o" + soft hyphen em "pala­vra"
    md = "pala\u00advra com integra\u00c3\u00a7\u00c3\u00a3o"
    resultado, avisos = aplicar_pipeline_qualidade(md, f)
    assert "palavra" in resultado
    assert "integração" in resultado
    assert avisos == []


def test_aplicar_pipeline_sem_llm_por_padrao(tmp_path):
    """Sem usar_llm/llm_fallback, LLM não é chamado (mesmo com avisos)."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF" + b"x" * 5000)
    md = "\ufffd\ufffd texto corrompido"
    resultado, avisos = aplicar_pipeline_qualidade(md, f, usar_llm=False, llm_fallback=False)
    assert isinstance(avisos, list)
    assert len(avisos) > 0  # detecta U+FFFD


def test_pipeline_repassa_config_llm(tmp_path):
    """llm_config do pipeline chega intacto ao disponivel/melhorar_markdown.

    É o elo entre as flags --llm-url/--llm-modelo e o enhancer: sem isto,
    a precedência flag > env se perde no caminho batch → quality.
    """
    from unittest.mock import patch

    from core.llm_enhancer import ConfigLLM

    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF" + b"x" * 5000)
    md = "texto com \ufffd\ufffd corrompido"  # U+FFFD sobrevive à limpeza → avisos
    config = ConfigLLM(url="http://localhost:11434/v1", modelo="llama3.2-vision")
    recebidos = []

    def fake_disponivel(c=None):
        recebidos.append(("disponivel", c))
        return True

    def fake_melhorar(texto, origem, c=None):
        recebidos.append(("melhorar", c))
        return texto, []

    with (
        patch("core.llm_enhancer.disponivel", side_effect=fake_disponivel),
        patch("core.llm_enhancer.melhorar_markdown", side_effect=fake_melhorar),
    ):
        aplicar_pipeline_qualidade(md, f, llm_fallback=True, llm_config=config)

    assert len(recebidos) == 2
    assert all(c is config for _, c in recebidos)
