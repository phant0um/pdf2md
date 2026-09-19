"""
Pipeline de qualidade para saídas de conversão.

Dois estágios aplicados a todo output antes de salvar:

  1. LIMPEZA automática  — remove artefatos silenciosos que corrompem o texto
     visualmente sem levantar exceção no conversor:
       • Hifens suaves (U+00AD) — causam quebra de palavras em PDFs hifenizados
       • Chars de largura zero (U+200B/C/D, U+FEFF mid-string) — deslocam letras
       • Espaços não-quebráveis (U+00A0) → espaço normal
       • Ligaduras perdidas (U+FFFD de glifo fl/fi/ff sem ToUnicode) —
         reparadas contra o texto cru do próprio PDF (só no caminho PDF)

  2. VALIDAÇÃO — detecta problemas que a limpeza não pode corrigir sozinha e
     retorna lista de avisos para exibição no CLI e GUI:
       • Mojibake PT-BR (Latin-1 decodificado como UTF-8)
       • Chars de substituição U+FFFD (encoding definitivamente corrompido)
       • Output muito curto para o tamanho do arquivo (extração falhou)
       • Hifens suaves residuais após limpeza (PDF com hifenização manual pesada)

ORDEM OBRIGATÓRIA no pipeline:
  1. corrigir_mojibake(texto)   — deve rodar ANTES de limpar_artefatos
  2. limpar_artefatos(texto)    — remove soft hyphens etc.
  3. validar_qualidade(md, ...) — detecta problemas residuais

Motivo: o mojibake de "í" contém U+00AD (soft hyphen) como segundo byte.
Se limpar_artefatos rodar primeiro, o padrão "Ã\xad" vira "Ã" e a correção
de mojibake não consegue mais detectar.
"""
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from core.llm_enhancer import ConfigLLM

# ── Tabela de mojibake PT-BR (gerada programaticamente) ─────────────────────
# Mojibake ocorre quando texto Latin-1/cp1252 é decodificado como UTF-8.
# Cada char Latin-1 vira 2 bytes UTF-8 que, relidos como Latin-1, produzem
# "Ã" + outro char.
#
# Geramos a tabela computando:  char.encode('utf-8').decode('latin-1') → padrão errado
# Isto evita literais de encoding ambíguos no código-fonte.

def _build_mojibake_table(chars: str) -> list[tuple[str, str]]:
    """Gera tabela (padrão_errado, char_correto) para os chars fornecidos."""
    result: list[tuple[str, str]] = []
    for c in chars:
        try:
            errado = c.encode("utf-8").decode("latin-1")
            result.append((errado, c))
        except (UnicodeDecodeError, UnicodeEncodeError):
            pass
    return result


# Chars acentuados PT-BR mais frequentes — minúsculas + maiúsculas
_PT_BR_CHARS = "ãçéóáíúõâêôàÃÇÉÓÁÍÚÕÂÊÔÀ"
_MOJIBAKE: list[tuple[str, str]] = _build_mojibake_table(_PT_BR_CHARS)

# Strings de detecção rápida (só os padrões errados)
_MOJIBAKE_DETECTORES: list[str] = [errado for errado, _ in _MOJIBAKE]

# Regex compilado para detecção E correção de mojibake em uma única passada
# (antes: ~20× str.count + str.replace = ~40 travessias O(n); agora 1 regex).
_MOJIBAKE_RE = re.compile("|".join(re.escape(p) for p in _MOJIBAKE_DETECTORES))
_MOJIBAKE_PARA: dict[str, str] = dict(_MOJIBAKE)

# Thresholds de validação (magic numbers nomeados)
_MIN_KB_AVISO_CURTO = 10
_MIN_CHARS_AVISO_CURTO = 100
_MAX_SOFT_HYPHENS = 5

# Ligaduras tipográficas candidatas ao reparo de U+FFFD.
# A aceitação exige match no dicionário do documento, então a ordem só
# desempata ambiguidade genuína (ex.: "�ies" → "flies" vs "fies").
_LIGADURAS = ("fl", "fi", "ff", "ffi", "ffl", "ft", "st")

# Token alfanumérico contendo pelo menos um U+FFFD. [^\W_] = letra/dígito
# Unicode sem underscore — preserva acentuação PT-BR no token.
_TOKEN_FFFD_RE = re.compile(r"[^\W_]*�[^\W_]*")

# Mínimo de letras reais no token para tentar reparo. Abaixo disso o token
# não carrega contexto suficiente (ex.: "[�]" de fonte matemática) e
# qualquer candidato casaria com o dicionário por acidente.
_MIN_LETRAS_REPARO = 2

# Extrai palavras do texto de referência para montar o dicionário do documento.
_PALAVRA_RE = re.compile(r"[^\W_]+")

# Tabela de tradução compilada para limpar_artefatos — 1 passada str.translate
# (antes: 6× str.replace = 6 travessias O(n) do texto inteiro).
_ARTEFATOS_TRADUCAO = str.maketrans({
    "\u00ad": None,   # SOFT HYPHEN → remove
    "\u200b": None,   # ZERO WIDTH SPACE → remove
    "\u200c": None,   # ZERO WIDTH NON-JOINER → remove
    "\u200d": None,   # ZERO WIDTH JOINER → remove
    "\ufeff": None,   # BOM → remove
    "\u00a0": " ",    # NO-BREAK SPACE → espaço normal
})


# ── 1a. Correção de mojibake (deve rodar ANTES de limpar_artefatos) ──────────

def corrigir_mojibake(texto: str) -> tuple[str, int]:
    """
    Corrige automaticamente mojibake PT-BR comum.

    Deve ser chamado ANTES de limpar_artefatos: o mojibake de "í" contém
    U+00AD (soft hyphen), que limpar_artefatos removeria antes da correção.

    Aplica substituições da tabela _MOJIBAKE em sequência. Seguro para
    documentos em português: os padrões "Ã£", "Ã§" etc. nunca aparecem
    intencionalmente em texto PT-BR correto — são sempre artefatos de encoding.

    Args:
        texto: String possivelmente corrompida.

    Returns:
        (texto_corrigido, n_substituições_realizadas)
    """
    n_total = 0

    def _substituir(m: re.Match[str]) -> str:
        nonlocal n_total
        n_total += 1
        return _MOJIBAKE_PARA[m.group(0)]

    # re.sub com callback: 1 passada O(n) para corrigir e contar tudo
    # (antes: count+replace por padrão — ~40 travessias do texto inteiro).
    texto = _MOJIBAKE_RE.sub(_substituir, texto)
    return texto, n_total


# ── 1b. Limpeza de artefatos (deve rodar APÓS corrigir_mojibake) ─────────────

def limpar_artefatos(texto: str) -> str:
    """
    Remove artefatos comuns de extração de PDF que corrompem o texto visualmente.

    Deve ser chamado APÓS corrigir_mojibake (ver docstring do módulo).

    Artefatos removidos:
    - U+00AD: SOFT HYPHEN — invisível em editores, mas quebra palavras
      em renderizadores que o respeitam (Obsidian, browsers, Word)
    - U+200B: ZERO WIDTH SPACE
    - U+200C: ZERO WIDTH NON-JOINER
    - U+200D: ZERO WIDTH JOINER
    - U+FEFF: BOM — removido em qualquer posição (é artefato de encoding,
      não conteúdo; o decode do leitor já consumiu o BOM inicial quando ele
      existia no arquivo original)
    - U+00A0: NO-BREAK SPACE → espaço ASCII normal
    - Linhas que ficaram com só whitespace → linha vazia (preserva parágrafos)

    Args:
        texto: String extraída pelo conversor (já com mojibake corrigido).

    Returns:
        String limpa. Nunca modifica conteúdo semântico real.
    """
    texto = texto.translate(_ARTEFATOS_TRADUCAO)

    # Colapsa linhas que ficaram só com whitespace (preserva estrutura de parágrafos)
    linhas = [linha if linha.strip() else "" for linha in texto.split("\n")]
    texto = "\n".join(linhas)

    return texto


# ── 1c. Reparo de ligaduras perdidas (U+FFFD) ───────────────────────────────

def reparar_ligaduras(md: str, referencia: str) -> tuple[str, int]:
    """
    Repara U+FFFD originado de glifo de ligadura (fl, fi, ff...) não mapeado.

    Motivo: o engine de layout do pymupdf4llm devolve U+FFFD para glifos de
    ligadura em PDFs cuja fonte não traz ToUnicode ("Work\ufffdow", "re\ufffdect"),
    enquanto o `page.get_text()` clássico do PyMuPDF resolve o mesmo glifo
    corretamente. Usamos esse texto cru como DICIONÁRIO DO PRÓPRIO DOCUMENTO —
    nunca adivinhamos a expansão.

    O U+FFFD substitui a ligadura inteira, 1-para-1: "Work\ufffdow" vira
    "Workflow" por substituição direta. Um candidato só é aceito se a palavra
    resultante existir, inteira, no dicionário. Token sem candidato válido fica
    INTACTO e continua disparando o aviso de validar_qualidade — nunca é
    truncado nem aproximado.

    Limitação conhecida: o dicionário é por-documento. Palavra cuja grafia
    correta só aparece dentro de região de imagem não tem referência no texto
    cru e não é reparada.

    Args:
        md: Markdown extraído, possivelmente com U+FFFD.
        referencia: Texto cru do mesmo documento (fonte da verdade).

    Returns:
        (md_reparado, n_tokens_reparados)
    """
    if "\ufffd" not in md or not referencia:
        return md, 0

    dicionario = {p.lower() for p in _PALAVRA_RE.findall(referencia)}
    if not dicionario:
        return md, 0

    n_total = 0

    def _reparar(m: re.Match[str]) -> str:
        nonlocal n_total
        token = m.group(0)
        letras = token.replace("\ufffd", "")
        if len(letras) < _MIN_LETRAS_REPARO:
            return token

        # Token todo em caixa alta recebe ligadura em caixa alta:
        # "WORK\ufffdOW" → "WORKFLOW", nunca "WORKflOW".
        caixa_alta = letras.isupper()

        for ligadura in _LIGADURAS:
            candidato = token.replace(
                "\ufffd", ligadura.upper() if caixa_alta else ligadura
            )
            if candidato.lower() in dicionario:
                n_total += 1
                return candidato
        return token

    return _TOKEN_FFFD_RE.sub(_reparar, md), n_total


# ── 2. Validação ──────────────────────────────────────────────────────────────

def validar_qualidade(md: str, origem: Path) -> list[str]:
    """
    Valida qualidade do Markdown gerado e retorna lista de avisos humanos.

    Chamado APÓS corrigir_mojibake e limpar_artefatos — detecta problemas
    que as etapas de limpeza não puderam resolver.

    Verifica:
    1. Mojibake residual (padrões que a tabela não cobriu)
    2. Chars de substituição U+FFFD (encoding definitivamente corrompido)
    3. Output muito curto para o tamanho do arquivo (extração provavelmente falhou)
    4. Hifens suaves residuais acima de threshold (só se não foram removidos)

    Args:
        md: Markdown após limpeza completa.
        origem: Path do arquivo de origem (para comparar tamanho).

    Returns:
        Lista de strings de aviso. Vazia se qualidade OK.
    """
    avisos: list[str] = []

    if not md.strip():
        return []  # output vazio é ERRO, não aviso de qualidade

    # 1. Mojibake residual — regex compilado (1 passada vs 24 str.count)
    n_mojibake = len(_MOJIBAKE_RE.findall(md))
    if n_mojibake > 0:
        avisos.append(
            f"Encoding possivelmente corrompido — {n_mojibake} padrão(s) de "
            f"mojibake PT-BR residual(is) detectado(s)"
        )

    # 2. Chars de substituição U+FFFD
    n_fffd = md.count("\ufffd")
    if n_fffd > 0:
        avisos.append(
            f"{n_fffd} caractere(s) de substituição (U+FFFD) detectado(s) — "
            f"texto provavelmente corrompido por problema de encoding"
        )

    # 3. Output muito curto para o tamanho do arquivo
    try:
        tamanho_kb = origem.stat().st_size / 1024
        chars_util = len(md.strip())
        if tamanho_kb > _MIN_KB_AVISO_CURTO and chars_util < _MIN_CHARS_AVISO_CURTO:
            avisos.append(
                f"Output muito curto ({chars_util} chars) para arquivo de "
                f"{tamanho_kb:.0f} KB — possível falha na extração de texto"
            )
    except OSError:
        pass

    # 4. Hifens suaves residuais acima de threshold
    n_soft_hyphen = md.count("\u00ad")
    if n_soft_hyphen > _MAX_SOFT_HYPHENS:
        avisos.append(
            f"{n_soft_hyphen} hifens suaves (U+00AD) residuais — palavras podem "
            f"aparecer quebradas em alguns renderizadores (Obsidian, browsers)"
        )

    return avisos


# ── 3. Orquestração do pipeline completo ──────────────────────────────────────

def aplicar_pipeline_qualidade(
    md: str,
    origem: Path,
    usar_llm: bool = False,
    llm_fallback: bool = False,
    llm_config: "ConfigLLM | None" = None,
) -> tuple[str, list[str]]:
    """
    Aplica o pipeline completo de qualidade ao Markdown extraído.

    Ordem obrigatória (ver docstring do módulo):
    1. corrigir_mojibake — antes de limpar_artefatos
    2. limpar_artefatos — remove soft hyphens etc.
    3. validar_qualidade — detecta problemas residuais → avisos
    4. LLM enhancement (opcional) — melhora qualidade via LLM

    Args:
        md: Markdown bruto extraído pelo conversor.
        origem: Path do arquivo de origem (para contexto no LLM e validar_qualidade).
        usar_llm: Se True, sempre aplica LLM (--llm).
        llm_fallback: Se True, aplica LLM só quando há avisos (--llm-fallback).
        llm_config: ConfigLLM opcional (precedência flag > env > default).

    Returns:
        (md_tratado, avisos) — avisos é lista vazia se qualidade OK.
    """
    md, _ = corrigir_mojibake(md)
    md = limpar_artefatos(md)
    avisos = validar_qualidade(md, origem)

    if usar_llm or (llm_fallback and avisos):
        # Lazy import — reduz acoplamento: quality não depende de llm_enhancer no import-time
        from core.llm_enhancer import disponivel as llm_disponivel
        from core.llm_enhancer import melhorar_markdown

        if llm_disponivel(llm_config):
            md, avisos_llm = melhorar_markdown(md, origem, llm_config)
            avisos = validar_qualidade(md, origem) + avisos_llm
        elif usar_llm:
            avisos.append(
                "LLM não disponível — verifique PDF2MD_LLM_URL e se Ollama está rodando"
            )

    return md, avisos
