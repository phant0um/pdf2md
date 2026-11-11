"""
Utilitários de segurança e validação para pdf2md.
"""
import re
from enum import StrEnum
from pathlib import Path, PurePosixPath

# Casa qualquer path absoluto (1+ segmentos), incluindo segmentos com
# espaço — necessário porque o username default do macOS é "First Last"
# (com espaço) e a regex anterior ([^\s:]+) parava no primeiro espaço,
# vazando o segmento até ali (ex: "/Users/John Doe/..." → "John Doe"
# aparecia inteiro na saída em vez de ser redigido).
#
# Em vez de proibir espaço no segmento, a âncora passa a ser a ESTRUTURA
# de path absoluto ("/" seguido de 1+ chars) e os delimitadores reais de
# fim de path em mensagens de erro: aspas, parênteses, dois-pontos e
# control chars (CR/LF/tab). Um espaço SÓ é aceito dentro de um segmento
# quando a palavra imediatamente seguinte (sem outro espaço) leva direto
# a uma nova barra "/" — isso é o que distingue "John Doe/Desktop" (nome
# de pasta com espaço, mais path) de "origem.pdf para /outro/path.md"
# (dois paths distintos separados por texto comum da mensagem; sem essa
# checagem a regex gulosa uniria os dois em um único match).
#
# Isso também corrige dois bugs colaterais da regex antiga:
#   - single-segment "/mountpoint" nunca casava (exigia '/' + '/' final);
#   - "/nonexistent dir/file.pdf" não era redigido (espaço cortava a
#     captura antes mesmo de chegar no '/' final).
# O grupo opcional "~" no início preserva esse prefixo intacto quando
# presente (path relativo ao home já reduzido por sanitizar_mensagem_erro),
# evitando que a regex comprima também os segmentos intermediários.
_RE_PATH_ABSOLUTO = re.compile(
    r"(~)?(?:/(?:[^/\x00-\x1f:'\\\") ]| (?=[^/\x00-\x1f:'\\\") ]*/))+)+"
)

# Diretório de contas locais do macOS. Monkeypatchado nos testes.
_DIR_USUARIOS = Path("/Users")

# Lookahead de fim de segmento de path: o match só vale se o próximo caractere
# fecha o segmento (barra, espaço, aspas, dois-pontos, parêntese ou fim de string).
# Impede que "/Users/al" case o prefixo de "/Users/alice".
# Pontuação ("." "," ";") só fecha o segmento quando seguida de espaço ou fim de
# string (fim de frase): dentro de nome de conta ("john.doe", "john,bob") não fecha,
# senão o sufixo vazaria.
_FIM_SEGMENTO = r"(?=/|[\s:'\")]|[.,;](?:\s|$)|$)"

# Extensões suportadas — fonte única da verdade (converters importam daqui)
EXTENSOES_PDF: frozenset[str] = frozenset({".pdf"})
EXTENSOES_IMAGEM: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".tiff", ".tif", ".webp", ".bmp", ".heic"
})
EXTENSOES_DOC: frozenset[str] = frozenset({".doc", ".docx"})
EXTENSOES_PPTX: frozenset[str] = frozenset({".pptx"})
EXTENSOES_PLANILHA: frozenset[str] = frozenset({".xlsx", ".csv"})
EXTENSOES_PERMITIDAS: frozenset[str] = (
    EXTENSOES_PDF | EXTENSOES_IMAGEM | EXTENSOES_DOC | EXTENSOES_PPTX | EXTENSOES_PLANILHA
)


# ── Feature `--imagens` (PDF-only): política de imagens embutidas ─────────────

class ModoImagem(StrEnum):
    """
    Política de tratamento das imagens embutidas em PDFs (flag --imagens).

    PDF e DOCX: outros formatos ignoram a flag (warning no batch).
    """

    transcrever = "transcrever"  # default — OCR só de páginas-scan (byte-idêntico ao atual)
    extrair = "extrair"          # salva os assets e linka ![](assets/...) no MD
    ambos = "ambos"              # extrai + OCR da imagem como alt-text
    ignorar = "ignorar"          # descarta imagens sem OCR


# Limites anti resource-bomb (gate Sentinel, T6):
# PDF malicioso pode embutir 10k imagens ou 1 imagem de 2 GB.
_MAX_IMAGENS_PDF = 500           # máx. imagens extraídas por documento
_MAX_BYTES_IMAGEM = 50 * 1024 * 1024  # máx. bytes por imagem (50 MB)
_MAX_BYTES_RENDER_PAGINA = 50 * 1024 * 1024  # máx. bytes por render de página-scan


# ── Helpers de validação reutilizáveis (DRY — usados por todos conversores) ──

def _validar_existencia(path: Path) -> None:
    """Levanta FileNotFoundError com mensagem sanitizada se path não existe."""
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path.name}")


def _validar_extensao(path: Path, permitidas: frozenset[str]) -> None:
    """Levanta ValueError se a extensão de path não está no conjunto permitido."""
    if path.suffix.lower() not in permitidas:
        raise ValueError(f"Extensão não suportada: {path.suffix}")


def _sanitizar_celula_md(text: str) -> str:
    """Normaliza texto de célula para Markdown seguro (pipe escaping).

    Quebras de linha (LF, CR, CRLF) viram espaço — dentro da célula
    elas partiriam a linha da tabela.
    """
    return (
        text.strip()
        .replace("\r\n", " ")
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("|", "\\|")
    )


def _linha_tabela_md(celulas: list[str]) -> str:
    """Monta uma linha de tabela Markdown a partir de células já sanitizadas."""
    return "| " + " | ".join(celulas) + " |"


def validar_path_seguro(path: Path, base_permitida: Path | None = None) -> Path:
    """
    Valida que path não contém path traversal.
    Se base_permitida fornecida, garante que path está dentro dela.

    Args:
        path: Path a ser validado.
        base_permitida: Path base opcional para restrição.

    Returns:
        Path resolvido e validado.

    Raises:
        ValueError: Se path contém '..' ou está fora de base_permitida.
    """
    path_resolvido = path.resolve()
    # Detecta traversal por componente (não substring): captura segmentos '..'
    # reais sem falso positivo em nomes como "relatorio..final.pdf".
    if ".." in path.parts:
        raise ValueError(f"Path contém traversal inválido: {path.name}")
    if base_permitida:
        base_resolvida = base_permitida.resolve()
        try:
            path_resolvido.relative_to(base_resolvida)
        except ValueError as exc:
            # Não vaza o path absoluto do usuário na mensagem de erro
            raise ValueError(f"Path fora do diretório permitido: {path.name}") from exc
    return path_resolvido


def _usuarios_com_espaco() -> list[str]:
    """
    Nomes de contas locais com espaço em `_DIR_USUARIOS` (ex: "John Doe"),
    do mais longo para o mais curto. Diretório ausente ou sem permissão
    devolve lista vazia.
    """
    try:
        nomes = [p.name for p in _DIR_USUARIOS.iterdir() if " " in p.name]
    except OSError:
        return []
    return sorted(nomes, key=len, reverse=True)


def sanitizar_mensagem_erro(msg: str) -> str:
    """
    Redige qualquer caminho absoluto presente em uma mensagem de erro,
    substituindo-o pelo basename (nome do arquivo/diretório final).

    Mais robusto que `str.replace(str(origem), origem.name)`: aquela
    abordagem falha quando o path na mensagem de exceção não é
    byte-a-byte idêntico ao path original — ex. symlink resolvido
    (`/var/...` → `/private/var/...` no macOS), diferença de
    maiúsculas/minúsculas em filesystems case-insensitive, ou barra
    final. Esta função usa regex sobre QUALQUER path absoluto na
    mensagem, independente de sua origem (CWE-209).

    O prefixo correspondente ao home do usuário (`Path.home()`) é
    reduzido a `~` antes da regex de basename — assim mensagens como
    "/Users/alice/projeto/x.pdf" preservam o contexto relativo ao home
    ("~/projeto/x.pdf") em vez de virarem apenas "x.pdf".

    Paths absolutos de OUTRO usuário (ex: "/Users/bob/..." quando o
    home do usuário atual é "/Users/alice") são redigidos ao basename
    do arquivo, sem revelar o username de terceiro (CWE-209 — útil em
    volumes compartilhados). Nomes com espaço ("/Users/John Doe") são
    redigidos inteiros só se existirem em `/Users` (contas locais); um
    nome com espaço que não é conta local deixa a segunda palavra visível,
    o que não pode vir de erro de filesystem local.

    Args:
        msg: Mensagem de erro potencialmente contendo paths absolutos.

    Returns:
        Mensagem com paths absolutos substituídos pelo basename (ou por
        "~/..." quando o path está dentro do home do usuário).
    """
    home = str(Path.home())

    # Contas locais com espaço no nome: "/Users/John Doe" vira "/Users/[user]"
    # antes da troca do home e da regex, para o espaço não ser lido como fim de
    # path. Roda ANTES do home: com home "/Users/John", trocar o home primeiro
    # deixaria " Doe" visível. A conta que é o próprio home é pulada (ela vira "~").
    if "/Users/" in msg:
        for nome in _usuarios_com_espaco():
            if "/Users/" + nome == home:
                continue
            padrao = re.escape("/Users/" + nome) + _FIM_SEGMENTO
            msg = re.sub(padrao, "/Users/[user]", msg)

    # O match para no fim do segmento (home "/Users/al" não toca "/Users/alice").
    # Home "/" é ignorado: trocaria toda barra do path por "~".
    if home and home != "/" and home in msg:
        msg = re.sub(re.escape(home) + _FIM_SEGMENTO, "~", msg)

    def _substituir(m: re.Match[str]) -> str:
        full = m.group(0)
        # Se o match começa com "~", preserva o caminho relativo completo
        # (ex: "~/projeto/sub/x.pdf") em vez de reduzir a só o basename —
        # já não há mais informação sensível de path absoluto do sistema.
        if m.group(1):
            return full
        # Caso especial: "/Users/<username>" (exatamente 2 segmentos no
        # padrão macOS) — o basename IS o username, que seria vazado.
        # Redige para "[user]" em vez de expor o nome (CWE-209).
        # Username com espaço já foi trocado por "/Users/[user]" antes da
        # regex (ver _usuarios_com_espaco), então cai neste caso também.
        if full.startswith("/Users/") and full.count("/") == 2:
            return "[user]"
        # PurePosixPath.name extrai o último segmento mesmo quando os
        # segmentos intermediários contêm espaço (ex: "/Users/John Doe/
        # Desktop/x.pdf" → "x.pdf", sem vazar "John Doe"). Fallback para
        # o match completo no caso degenerado de path vazio (não deve
        # ocorrer dado que a regex exige ao menos um "/<algo>").
        return PurePosixPath(full).name or full

    return _RE_PATH_ABSOLUTO.sub(_substituir, msg)
