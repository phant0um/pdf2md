"""
Converte arquivos .docx e .doc em Markdown.

Estratégia:
- .docx → mammoth.convert_to_markdown() (estrutura preservada: headers, bold, listas)
- .doc  → textutil via subprocess (texto plano; nativo macOS, sempre em /usr/bin/textutil)

Imagens do .docx (T19 — unificação da política `--imagens` com o PDF):
- Antes: mammoth embutia base64 (data-URI) por padrão — inconsistente com o
  PDF (que descarta) e rejeitado na decisão D2 do ADR-0005.
- Agora: transcrever (default) descarta; extrair salva assets com posição
  preservada (o handler do mammoth insere o link no ponto exato do texto);
  ambos extrai + OCR como alt-text; ignorar descarta. A maquinaria de
  segurança (dedup D4, nomenclatura D5, limites, symlink) é a mesma de
  core/image_assets.py.
"""
import os
import re
import subprocess
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import IO, Protocol

from core.image_assets import (
    AssetImagem,
    ColetorAssets,
    ContextoAssets,
    preparar_assets_dir,
    registrar_asset,
)
from core.image_converter import alt_text_enxuto, ocr_bytes, wikilink_imagem
from core.utils import (  # fonte única da verdade (re-exportado)
    _MAX_BYTES_IMAGEM,
    EXTENSOES_DOC,
    ModoImagem,
    _validar_existencia,
    _validar_extensao,
    sanitizar_mensagem_erro,
)

# textutil é um utilitário nativo do macOS (parte do CoreServices), presente
# em todo macOS desde versões antigas, sempre em /usr/bin/textutil — diferente
# do antiword (Homebrew), não precisa de resolução de PATH para PyInstaller.
_TEXTUTIL_PATH = "/usr/bin/textutil"

# content-type do docx → extensão segura (allowlist do image_assets).
# Fora deste mapa: imagem descartada com aviso (não há conversor confiável).
_MIME_PARA_EXT = {
    "image/png": ".png",
    "image/x-png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
    "image/tiff": ".tiff",
    "image/x-tiff": ".tiff",
}


class _ImagemMammoth(Protocol):
    """Forma mínima do objeto Image do mammoth (lib sem stubs de tipos)."""

    alt_text: str | None
    content_type: str | None

    def open(self) -> AbstractContextManager[IO[bytes]]: ...


def doc_to_md(
    path: Path,
    modo_imagem: ModoImagem = ModoImagem.transcrever,
    assets_dir: Path | None = None,
    md_dir: Path | None = None,
    wikilinks: bool = False,
    prefixo_nome: str = "",
    avisos: list[str] | None = None,
) -> str:
    """
    Converte arquivo Word (.doc ou .docx) em string Markdown.

    Args:
        path: Caminho para o arquivo .doc ou .docx.
        modo_imagem: Política de imagens embutidas (ModoImagem). O default
            `transcrever` descarta imagens — o base64 embutido pelo mammoth
            foi removido (T19/ADR-0005 D2).
        assets_dir: Diretório dos assets extraídos (extrair/ambos).
            Default: `md_dir/<stem>_assets`.
        md_dir: Diretório do .md de saída — base dos links relativos.
        wikilinks: True em modo Obsidian → `![[nome]]`.
        prefixo_nome: Prefixo dos nomes de asset (diretórios compartilhados).
        avisos: Lista opcional onde avisos de extração são anexados.

    Returns:
        String Markdown com conteúdo extraído.

    Raises:
        FileNotFoundError: Se path não existe.
        ValueError: Se extensão não está em EXTENSOES_DOC.
        RuntimeError: Se conversão falha (arquivo corrompido, textutil ausente, etc).
    """
    _validar_existencia(path)

    sufixo = path.suffix.lower()
    _validar_extensao(path, EXTENSOES_DOC)

    if sufixo == ".docx":
        return _docx_para_md(path, _montar_contexto(
            path, modo_imagem, assets_dir, md_dir, wikilinks, prefixo_nome, avisos
        ))
    else:
        # .doc via textutil: só texto — modo de imagem não se aplica
        return _doc_para_md(path)


def _montar_contexto(
    path: Path,
    modo_imagem: ModoImagem,
    assets_dir: Path | None,
    md_dir: Path | None,
    wikilinks: bool,
    prefixo_nome: str,
    avisos: list[str] | None,
) -> ContextoAssets | None:
    """Constrói o contexto de assets do documento (None p/ transcrever)."""
    if modo_imagem in (ModoImagem.transcrever, ModoImagem.ignorar):
        return None

    # Resolvido desde o início: no macOS /var é symlink de /private/var e o
    # containment assert de caminho_seguro compara paths resolvidos.
    dir_assets = (assets_dir or (path.parent / f"{path.stem}_assets")).resolve()
    return ContextoAssets(
        modo=modo_imagem,
        coletor=ColetorAssets(prefixo=prefixo_nome),
        assets_dir=dir_assets,
        md_dir=(md_dir or dir_assets.parent).resolve(),
        wikilinks=wikilinks,
        avisos=avisos if avisos is not None else [],
    )


def _docx_para_md(path: Path, contexto: ContextoAssets | None) -> str:
    """Converte .docx via mammoth — preserva headers, negrito, listas, tabelas.

    O handler de imagem substitui o base64 padrão do mammoth: descarta
    (transcrever/ignorar) ou grava assets na posição exata do documento.
    """
    try:
        import mammoth
    except ImportError as exc:
        raise RuntimeError(
            "mammoth não encontrado. Instale com: pip install mammoth"
        ) from exc

    try:
        # transcrever/ignorar: descarta imagens — sem base64 (T19, D2).
        # O handler vazio impede o data-URI padrão do mammoth.
        if contexto is None:
            with open(path, "rb") as f:
                resultado = mammoth.convert_to_markdown(
                    f, convert_image=mammoth.images.img_element(_descartar_imagem)
                )
            return str(resultado.value).strip()

        preparar_assets_dir(contexto.assets_dir)
        handler = mammoth.images.img_element(_montar_handler(contexto))

        with open(path, "rb") as f:
            resultado = mammoth.convert_to_markdown(f, convert_image=handler)
        md = str(resultado.value)

        if contexto.wikilinks:
            md = _converter_wikilinks(
                md, contexto.coletor, usar_alt=contexto.modo == ModoImagem.ambos,
            )
        return md.strip()
    except Exception as exc:
        raise RuntimeError(
            f"Falha ao converter {path.name} — arquivo corrompido ou formato inválido"
        ) from exc


def _descartar_imagem(image: _ImagemMammoth) -> dict[str, str]:
    """Handler do mammoth que descarta a imagem (transcrever/ignorar)."""
    return {}


# Marcador de src para o modo wikilink: o mammoth sempre emite `![alt](src)`;
# o src temporário carrega o marcador para o pós-processo converter SÓ os
# links que geramos — impossível de colidir com texto do usuário (ao
# contrário de usar o nome puro do asset).
_MARCADOR_WIKILINK = "pdf2md-asset://"


def _alt_ocr(asset: AssetImagem) -> str:
    """Alt-text do OCR seguro para `![alt](src)` e `![[nome|alt]]`.

    `[`, `]` e `\\` viram espaço: um `]` no texto do OCR fechava o alt cedo
    e o marcador `pdf2md-asset://` vazava para o Markdown final.
    """
    bruto = alt_text_enxuto(ocr_bytes(asset.dados, asset.extensao))
    limpo = re.sub(r"[\[\]\\]", " ", bruto)
    limpo = re.sub(r"\s+", " ", limpo).strip()
    return limpo or "imagem"


def _montar_handler(contexto: ContextoAssets) -> Callable[[_ImagemMammoth], dict[str, str]]:
    """Closure do mammoth: recebe a imagem do documento e devolve atributos <img>.

    `src` é o caminho relativo (ou marcador+nome, em wikilinks) do asset
    gravado. `{}` descarta a imagem (transcrever/ignorar/limite excedido).
    """
    limite_avisado = False

    def converter_imagem(image: _ImagemMammoth) -> dict[str, str]:
        nonlocal limite_avisado
        if contexto.coletor.atingiu_limite():
            if not limite_avisado:
                contexto.avisos.append(
                    "limite de imagens por documento excedido — imagens restantes descartadas"
                )
                limite_avisado = True
            return {}

        with image.open() as f:
            # FIX 2: checa o tamanho ANTES de ler — um docx malicioso com
            # imagem de 2 GB materializava tudo em memória no f.read()
            # (o check de 50 MB em registrar_asset vinha DEPOIS da leitura
            # e derrubava o processo). seek/tell é O(1) e não lê bytes.
            f.seek(0, 2)
            if f.tell() > _MAX_BYTES_IMAGEM:
                contexto.avisos.append(
                    f"imagem {contexto.coletor.total + 1} do documento excede "
                    f"{_MAX_BYTES_IMAGEM // (1024 * 1024)} MB — ignorada"
                )
                return {}
            f.seek(0)
            dados = f.read()

        ext = _MIME_PARA_EXT.get(image.content_type or "")
        if ext is None:
            contexto.avisos.append(
                f"imagem {contexto.coletor.total + 1} tem formato não suportado "
                f"({image.content_type or 'desconhecido'}) — descartada"
            )
            contexto.coletor.total += 1
            return {}

        asset, aviso = registrar_asset(
            contexto.assets_dir, contexto.coletor,
            f"img_{contexto.coletor.total + 1:04d}", ext, dados,
            f"{contexto.coletor.total + 1} do documento",
        )
        if aviso:
            contexto.avisos.append(aviso)
        if asset is None:
            return {}

        fazer_ocr = contexto.modo == ModoImagem.ambos and not asset.duplicado
        if contexto.wikilinks:
            marcador = {"src": f"{_MARCADOR_WIKILINK}{asset.nome}"}
            if fazer_ocr:
                marcador["alt"] = _alt_ocr(asset)
            return marcador
        src = {"src": os.path.relpath(asset.caminho_disco, contexto.md_dir)}
        if fazer_ocr:
            src["alt"] = _alt_ocr(asset)
        return src

    return converter_imagem


def _converter_wikilinks(md: str, coletor: ColetorAssets, usar_alt: bool = False) -> str:
    """![](...) → ![[...]] apenas para links com o marcador gerado por nós (D1).

    `usar_alt` (modo ambos): o alt do OCR vira alias, `![[nome|texto]]`.
    Sem ele (extrair) o wikilink sai puro, mesmo que o .docx traga alt próprio.
    """
    padrao = re.compile(
        rf"!\[([^\]]*)\]\({re.escape(_MARCADOR_WIKILINK)}([^)]*)\)"
    )
    nomes = {asset.nome for asset in coletor.cache.values()}

    def _sub(m: re.Match[str]) -> str:
        nome = m.group(2)
        if nome in nomes:
            return wikilink_imagem(nome, m.group(1)) if usar_alt else f"![[{nome}]]"
        return m.group(0)

    return padrao.sub(_sub, md)


def _decodificar_textutil(dados: bytes) -> str:
    """
    Decodifica a saída do textutil defensivamente.

    textutil emite UTF-8 nativamente (diferente do antiword que usava
    Latin-1), mas mantemos errors='replace' como rede de segurança caso
    o .doc de origem contenha bytes em outro encoding que o textutil
    repasse sem reconverter.
    """
    return dados.decode("utf-8", errors="replace")


def _doc_para_md(path: Path) -> str:
    """
    Converte .doc despachando pela assinatura do arquivo (magic bytes):
    - "PK" (zip) → na verdade é um .docx/Word-XML renomeado → mammoth
    - OLE binário (D0 CF 11 E0) ou outro → textutil (nativo macOS)

    Despachar pelo conteúdo (e não por "textutil falhou → tenta docx") evita
    tentativas inúteis e preserva o stderr real do textutil na mensagem de erro.
    """
    with open(path, "rb") as f:
        assinatura = f.read(4)

    # .docx (zip) disfarçado de .doc — roteia direto pro mammoth
    if assinatura[:2] == b"PK":
        return _docx_para_md(path, None)

    try:
        # capture_output sem text=True → bytes, decodificados defensivamente
        # (textutil normalmente emite UTF-8, mas a cascata cobre exceções).
        resultado = subprocess.run(
            [_TEXTUTIL_PATH, "-convert", "txt", "-stdout", str(path)],
            capture_output=True,
            timeout=30,
        )
        if resultado.returncode == 0:
            return _decodificar_textutil(resultado.stdout).strip()
        # OLE binário mas textutil falhou — erro claro com o stderr real,
        # com o path absoluto redigido (CWE-209).
        stderr_txt = sanitizar_mensagem_erro(
            _decodificar_textutil(resultado.stderr).strip()
        )
        raise RuntimeError(
            f"Falha ao converter {path.name} — textutil: {stderr_txt}"
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"textutil não encontrado em {_TEXTUTIL_PATH}. "
            "pdf2md requer macOS (textutil é nativo do sistema)."
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"Timeout ao converter {path.name} — arquivo muito grande ou corrompido"
        ) from exc
