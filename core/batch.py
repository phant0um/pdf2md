"""
Orquestra conversão em batch de múltiplos arquivos (PDFs, imagens, Word, PPTX, planilhas).
Paraleliza via ThreadPoolExecutor (compatível com PyInstaller one-file).
"""
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from core.converter import pdf_to_md
from core.doc_converter import doc_to_md
from core.formatter import add_obsidian_frontmatter
from core.image_assets import slugificar_ascii
from core.image_converter import image_to_md
from core.llm_enhancer import ConfigLLM
from core.pptx_converter import pptx_to_md
from core.quality import aplicar_pipeline_qualidade
from core.utils import (
    EXTENSOES_DOC,
    EXTENSOES_IMAGEM,
    EXTENSOES_PDF,
    EXTENSOES_PERMITIDAS,
    EXTENSOES_PLANILHA,
    EXTENSOES_PPTX,
    ModoImagem,
    sanitizar_mensagem_erro,
    validar_path_seguro,
)
from core.xlsx_converter import planilha_to_md

# Registry de conversores: mapeia frozenset de extensões → função conversora.
# Adicionar um formato novo = uma linha aqui (antes eram 5 ramos if/elif).
_CONVERSORES: list[tuple[frozenset[str], Callable[[Path], str]]] = [
    (EXTENSOES_PDF, pdf_to_md),
    (EXTENSOES_IMAGEM, image_to_md),
    (EXTENSOES_DOC, doc_to_md),
    (EXTENSOES_PPTX, pptx_to_md),
    (EXTENSOES_PLANILHA, planilha_to_md),
]


class StatusArquivo(Enum):
    AGUARDANDO = "aguardando"
    PROCESSANDO = "processando"
    CONCLUIDO = "concluido"
    ERRO = "erro"
    IGNORADO = "ignorado"


@dataclass
class ResultadoArquivo:
    origem: Path
    destino: Path | None
    status: StatusArquivo
    erro: str | None = None
    avisos: list[str] = field(default_factory=list)  # avisos de qualidade (não impedem CONCLUIDO)


def _nome_destino_unico(arq: Path, usados: set[str]) -> str:
    """
    Gera nome de saída .md único dentro do batch.

    O primeiro arquivo de um stem mantém `<stem>.md`. Colisões subsequentes
    (mesmo stem, extensão diferente — ex: relatorio.pdf + relatorio.docx)
    recebem o sufixo da extensão de origem para evitar sobrescrita silenciosa
    sob ThreadPoolExecutor: `<stem>-<ext>.md`, depois `<stem>-<ext>-2.md`, etc.

    Determinístico porque a lista de arquivos é percorrida em ordem (sorted).
    """
    base = arq.stem + ".md"
    if base not in usados:
        return base
    ext = arq.suffix.lstrip(".").lower()
    candidato = f"{arq.stem}-{ext}.md"
    contador = 2
    while candidato in usados:
        candidato = f"{arq.stem}-{ext}-{contador}.md"
        contador += 1
    return candidato


def _processar_arquivo(
    origem_str: str,
    destino_str: str,
    obsidian: bool,
    sobrescrever: bool,
    usar_llm: bool = False,
    llm_fallback: bool = False,
    ignorar_margens: float = 0.0,
    llm_config: ConfigLLM | None = None,
    modo_imagem: ModoImagem = ModoImagem.transcrever,
    assets_dir_str: str | None = None,
) -> dict[str, Any]:
    """
    Worker executado em ThreadPoolExecutor. Recebe/retorna tipos simples
    (strings, dict) para uma fronteira de dados limpa entre as threads.
    """
    origem = Path(origem_str)
    destino = Path(destino_str)

    if not sobrescrever and destino.exists():
        return _resultado_ignorado(origem_str, destino_str)

    # LLM com visão só com opt-in explícito (--llm / --llm-fallback); sem ele, None.
    visao = (llm_config or ConfigLLM()) if (usar_llm or llm_fallback) else None

    try:
        avisos_imagens: list[str] = []
        md = _converter_arquivo(
            origem, ignorar_margens, modo_imagem, destino,
            obsidian, assets_dir_str, avisos_imagens, visao,
        )
        if md is None:
            return _resultado_ignorado(origem_str, None)
        md, avisos = aplicar_pipeline_qualidade(
            md, origem, usar_llm, llm_fallback, llm_config
        )
        avisos = avisos_imagens + avisos

        if obsidian:
            md = add_obsidian_frontmatter(md, origem)

        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(md, encoding="utf-8")

        return {
            "origem": origem_str,
            "destino": destino_str,
            "status": StatusArquivo.CONCLUIDO.value,
            "erro": None,
            "avisos": avisos,
        }
    except Exception as exc:
        return _resultado_erro(origem_str, exc)


def _converter_arquivo(
    origem: Path,
    ignorar_margens: float = 0.0,
    modo_imagem: ModoImagem = ModoImagem.transcrever,
    destino: Path | None = None,
    obsidian: bool = False,
    assets_dir_str: str | None = None,
    avisos: list[str] | None = None,
    visao: ConfigLLM | None = None,
) -> str | None:
    """
    Despacha para o conversor correto baseado na extensão. None = não suportado.

    `--imagens` vale para PDF e DOCX: só pdf_to_md/doc_to_md recebem o
    modo; outros formatos ganham um aviso, não erro.

    `visao` (opt-in do LLM com visão) vai só para pdf_to_md e image_to_md.
    """
    sufixo = origem.suffix.lower()
    conversor = next(
        (fn for exts, fn in _CONVERSORES if sufixo in exts), None
    )
    if conversor is None:
        return None
    if conversor is pdf_to_md:
        return _pdf_to_md(
            origem, ignorar_margens, modo_imagem,
            destino, obsidian, assets_dir_str, avisos, visao,
        )
    if conversor is doc_to_md:
        return _docx_to_md(
            origem, modo_imagem, destino, obsidian, assets_dir_str, avisos
        )
    if modo_imagem != ModoImagem.transcrever and avisos is not None:
        avisos.append("--imagens só se aplica a PDF e DOCX — modo ignorado para este arquivo")
    if conversor is image_to_md:
        return image_to_md(origem, visao=visao)
    return conversor(origem)


def _resolver_assets(
    origem: Path, destino: Path | None, obsidian: bool, assets_dir_str: str | None
) -> tuple[Path, str]:
    """
    Resolve o destino dos assets (D1) e o prefixo de nomes.

    - default: `<stem>_assets/` ao lado do .md
    - `--assets-dir` explícito ou Obsidian: diretório COMPARTILHADO →
      prefixo por stem — usa o stem do .md DESTINO, que o batch já tornou
      único (`_nome_destino_unico`): dois PDFs de origens distintas com o
      mesmo nome viram `a.md`/`a-pdf.md` → prefixos `a__`/`a-pdf__`, sem
      sobrescrever assets um do outro sob ThreadPool.

    No batch, `destino` é o caminho do .md (arquivo) — o diretório é .parent.
    """
    destino_efetivo = destino.parent if destino else origem.parent
    if assets_dir_str:
        return Path(assets_dir_str), _prefixo_seguro(destino, origem)
    if obsidian:
        return destino_efetivo / "attachments", _prefixo_seguro(destino, origem)
    return destino_efetivo / f"{origem.stem}_assets", ""


def _prefixo_seguro(destino: Path | None, origem: Path) -> str:
    """Prefixo de nomes com caracteres garantidamente ASCII (FIX 1).

    O stem pode conter espaço/acento ("Relatório Final") — usado cru,
    o prefixo violava o regex de nome seguro de `caminho_seguro` e o
    arquivo inteiro virava ERRO em --obsidian/--assets-dir. Aplica
    slugificar_ascii (mesma regra de `doc_converter._montar_handler`,
    que recebe o prefixo via ContextoAssets).
    """
    base = destino.stem if destino else origem.stem
    return slugificar_ascii(f"{base}__")


def _kwargs_assets(
    origem: Path,
    modo_imagem: ModoImagem,
    destino: Path | None,
    obsidian: bool,
    assets_dir_str: str | None,
    avisos: list[str] | None,
) -> dict[str, Any]:
    """Kwargs comuns de `--imagens` para os conversores (D1 + prefixo)."""
    assets, prefixo = _resolver_assets(origem, destino, obsidian, assets_dir_str)
    return {
        "modo_imagem": modo_imagem,
        "assets_dir": assets,
        "md_dir": destino.parent if destino else origem.parent,
        "wikilinks": obsidian,
        "prefixo_nome": prefixo,
        "avisos": avisos,
    }


def _pdf_to_md(
    origem: Path,
    ignorar_margens: float,
    modo_imagem: ModoImagem,
    destino: Path | None,
    obsidian: bool,
    assets_dir_str: str | None,
    avisos: list[str] | None,
    visao: ConfigLLM | None = None,
) -> str:
    """Chama pdf_to_md com os assets resolvidos (default byte-idêntico)."""
    if modo_imagem == ModoImagem.transcrever:
        return pdf_to_md(origem, ignorar_margens=ignorar_margens, visao=visao)
    return pdf_to_md(
        origem,
        ignorar_margens=ignorar_margens,
        visao=visao,
        **_kwargs_assets(origem, modo_imagem, destino, obsidian, assets_dir_str, avisos),
    )


def _docx_to_md(
    origem: Path,
    modo_imagem: ModoImagem,
    destino: Path | None,
    obsidian: bool,
    assets_dir_str: str | None,
    avisos: list[str] | None,
) -> str:
    """Chama doc_to_md com os assets resolvidos (default descarta imagens)."""
    if modo_imagem == ModoImagem.transcrever:
        return doc_to_md(origem)
    return doc_to_md(
        origem,
        **_kwargs_assets(origem, modo_imagem, destino, obsidian, assets_dir_str, avisos),
    )


def _resultado_ignorado(origem_str: str, destino_str: str | None) -> dict[str, Any]:
    """Retorna dict de resultado para arquivo ignorado (destino já existe ou extensão não suportada)."""
    return {
        "origem": origem_str,
        "destino": destino_str,
        "status": StatusArquivo.IGNORADO.value,
        "erro": None,
        "avisos": [],
    }


def _resultado_erro(origem_str: str, exc: Exception) -> dict[str, Any]:
    """Retorna dict de resultado para arquivo com erro (path sanitizado)."""
    msg = sanitizar_mensagem_erro(str(exc))
    return {
        "origem": origem_str,
        "destino": None,
        "status": StatusArquivo.ERRO.value,
        "erro": msg,
        "avisos": [],
    }


def batch_convert(
    origem: Path | list[Path],
    destino: Path,
    workers: int = 4,
    sobrescrever: bool = False,
    vault: Path | None = None,
    obsidian: bool = False,
    usar_llm: bool = False,
    llm_fallback: bool = False,
    ignorar_margens: float = 0.0,
    llm_config: ConfigLLM | None = None,
    modo_imagem: ModoImagem = ModoImagem.transcrever,
    assets_dir: Path | None = None,
    ao_iniciar: Callable[[Path], None] | None = None,
    ao_concluir: Callable[[ResultadoArquivo], None] | None = None,
) -> list[ResultadoArquivo]:
    """
    Converte todos os arquivos suportados em `origem` para Markdown em `destino`.

    Comportamento:
    - Se `origem` é arquivo único: processa só ele
    - Se `origem` é diretório: processa só o nível raiz (não recursivo)
    - Se `origem` é lista: ela é o conjunto de arquivos (uma única chamada
      converte tudo, com dedup de nome e paralelismo). Ausente ou diretório
      vira ERRO naquele item; o restante do lote segue.
    - Arquivos com extensão não suportada: StatusArquivo.IGNORADO (sem erro)
    - `sobrescrever=False`: pula arquivos já existentes no destino
    - `vault` definido: output vai direto para `vault/` (cria se não existir)
    - `obsidian=True` (ou vault definido): aplica frontmatter antes de salvar
    - Paraleliza via `concurrent.futures.ThreadPoolExecutor(max_workers=workers)`
    - Erros individuais não interrompem o batch — capturados em ResultadoArquivo.erro
    - `ao_iniciar(origem)` roda na thread do worker, ao começar cada arquivo
    - `ao_concluir(resultado)` roda na thread chamadora, uma vez por arquivo
      (inclusive IGNORADO e os ERRO de lista ausente)

    Args:
        origem: Arquivo, diretório ou lista de arquivos de entrada.
        destino: Diretório de saída (criado se não existir). Ignorado se vault definido.
        workers: Número de threads paralelas.
        sobrescrever: Se True, sobrescreve MDs existentes.
        vault: Path para raiz do Obsidian vault. Output vai direto para vault/.
        obsidian: Se True, adiciona frontmatter Obsidian ao MD gerado.
        ao_iniciar: Callback de progresso ao iniciar cada arquivo (worker).
        ao_concluir: Callback de progresso ao concluir cada arquivo.

    Returns:
        Lista de ResultadoArquivo com status de cada arquivo processado.

    Raises:
        FileNotFoundError: Se `origem` (Path) não existe.
        NotADirectoryError: Se `vault` definido mas não é diretório.
    """
    if isinstance(origem, Path):
        validar_path_seguro(origem)

        if not origem.exists():
            raise FileNotFoundError(f"Origem não encontrada: {origem.name}")

    if vault is not None:
        if not vault.is_dir():
            raise NotADirectoryError(f"Vault inválido: {vault.name} não é um diretório")
        destino = vault
        obsidian = True

    destino.mkdir(parents=True, exist_ok=True)

    arquivos = _coletar_arquivos(origem)
    tarefas, resultados = _preparar_tarefas(
        arquivos, destino, lista_explicita=isinstance(origem, list),
    )
    if ao_concluir is not None:
        for descartado in resultados:
            ao_concluir(descartado)
    resultados.extend(_executar_paralelo(
        tarefas, workers, obsidian, sobrescrever, usar_llm, llm_fallback,
        ignorar_margens, llm_config, modo_imagem, assets_dir,
        ao_iniciar, ao_concluir,
    ))

    return resultados


def _coletar_arquivos(origem: Path | list[Path]) -> list[Path]:
    """Coleta arquivos da origem (arquivo único, diretório ou lista) e valida traversal."""
    if isinstance(origem, list):
        arquivos = list(origem)
    else:
        arquivos = [origem] if origem.is_file() else sorted(origem.iterdir())
    for arq in arquivos:
        validar_path_seguro(arq)
    return arquivos


def _preparar_tarefas(
    arquivos: list[Path], destino: Path, lista_explicita: bool = False,
) -> tuple[list[tuple[Path, Path]], list[ResultadoArquivo]]:
    """Separa arquivos suportados (tarefas) de não-suportados (ignorados).

    Com `lista_explicita` (origem é lista), ausente ou diretório vira ERRO
    em vez de ser pulado em silêncio — o chamador pediu aquele arquivo.
    """
    tarefas: list[tuple[Path, Path]] = []
    descartados: list[ResultadoArquivo] = []
    nomes_usados: set[str] = set()

    for arq in arquivos:
        if lista_explicita and not arq.is_file():
            descartados.append(ResultadoArquivo(
                origem=arq, destino=None, status=StatusArquivo.ERRO,
                erro=sanitizar_mensagem_erro(f"Arquivo não encontrado: {arq.name}"),
            ))
            continue
        if arq.is_dir():
            continue
        if arq.suffix.lower() not in EXTENSOES_PERMITIDAS:
            descartados.append(ResultadoArquivo(
                origem=arq, destino=None, status=StatusArquivo.IGNORADO, erro=None,
            ))
            continue
        nome_md = _nome_destino_unico(arq, nomes_usados)
        nomes_usados.add(nome_md)
        tarefas.append((arq, destino / nome_md))

    return tarefas, descartados


def _executar_paralelo(
    tarefas: list[tuple[Path, Path]],
    workers: int,
    obsidian: bool,
    sobrescrever: bool,
    usar_llm: bool,
    llm_fallback: bool,
    ignorar_margens: float = 0.0,
    llm_config: ConfigLLM | None = None,
    modo_imagem: ModoImagem = ModoImagem.transcrever,
    assets_dir: Path | None = None,
    ao_iniciar: Callable[[Path], None] | None = None,
    ao_concluir: Callable[[ResultadoArquivo], None] | None = None,
) -> list[ResultadoArquivo]:
    """Executa conversões em paralelo via ThreadPoolExecutor e coleta resultados."""
    if not tarefas:
        return []

    resultados_por_idx: dict[int, ResultadoArquivo] = {}

    def _trabalhar(arq: Path, dest: Path) -> dict[str, Any]:
        # Roda na thread do worker: avisa o início antes da conversão.
        if ao_iniciar is not None:
            ao_iniciar(arq)
        return _processar_arquivo(
            str(arq), str(dest), obsidian, sobrescrever,
            usar_llm, llm_fallback, ignorar_margens,
            llm_config,
            modo_imagem, str(assets_dir) if assets_dir else None,
        )

    with ThreadPoolExecutor(max_workers=workers) as executor:
        # Dict future → índice original: `as_completed` devolve na ordem de
        # conclusão, mas a tabela do CLI deve seguir a ordem de entrada.
        futures = {
            executor.submit(_trabalhar, arq, dest): idx
            for idx, (arq, dest) in enumerate(tarefas)
        }

        # Reordena por índice de entrada — a tabela do CLI fica determinística
        # (antes: ordem de conclusão, variava entre runs).
        for future in as_completed(futures):
            idx = futures[future]
            arq, _ = tarefas[idx]
            try:
                res = future.result()
                resultado = ResultadoArquivo(
                    origem=Path(res["origem"]),
                    destino=Path(res["destino"]) if res["destino"] else None,
                    status=StatusArquivo(res["status"]),
                    erro=res["erro"],
                    avisos=res.get("avisos", []),
                )
            except Exception as exc:
                resultado = ResultadoArquivo(
                    origem=arq,
                    destino=None,
                    status=StatusArquivo.ERRO,
                    erro=sanitizar_mensagem_erro(str(exc)),
                )
            resultados_por_idx[idx] = resultado
            if ao_concluir is not None:
                ao_concluir(resultado)

    return [resultados_por_idx[i] for i in range(len(tarefas))]
