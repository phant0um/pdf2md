"""
Interface de linha de comando para pdf2md.
Uso: pdf2md INPUT OUTPUT [opções]
     pdf2md arquivo.pdf saida/
     pdf2md arquivo.pptx saida/
     pdf2md planilha.xlsx saida/
     pdf2md pasta/docs/ pasta/markdowns/ --workers 8
     pdf2md docs/ --vault ~/Obsidian/vault-michel
     pdf2md llm modelos|testar --json
"""
import json
import os
import re
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import typer
from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
)
from rich.table import Table

from core.utils import (
    EXTENSOES_IMAGEM,
    EXTENSOES_PDF,
    ModoImagem,
    validar_path_seguro,
)

if TYPE_CHECKING:
    from core.batch import ResultadoArquivo

app = typer.Typer(
    name="pdf2md",
    help="Converte PDFs, imagens, Word, PPTX e planilhas em Markdown. Suporte a batch e Obsidian vault.",
    add_completion=False,
)

# Comandos de topo — o primeiro arg que não estiver aqui é tratado como ORIGEM
# (default-command: pdf2md <arquivo> <destino> dispensa o subcomando converter).
_COMANDOS_TOPO = frozenset({"converter", "llm", "--help", "-h", "--version", "--completion"})

# Opções do comando `converter` que consomem o próximo argumento como valor
# (FIX 5 — reordenação do default-command precisa pulá-las ao achar o
# primeiro positional; --imagens=extrair e afins não precisam, o valor vem
# junto na mesma flag).
_OPCOES_COM_VALOR = frozenset({
    "--workers", "-w",
    "--vault",
    "--ignorar-margens",
    "--llm-url",
    "--llm-modelo",
    "--imagens",
    "--assets-dir",
})

# Markup Rich usado nas mensagens de erro do console ([red]...[/red]) — o
# caminho --json emite o campo `erro` no stdout consumido pelo bridge Swift,
# que não entende tags Rich (FIX 9).
_RE_MARKUP_RICH = re.compile(r"\[/?[a-zA-Z][a-zA-Z0-9._ -]*?\]")

# Serializa escritas JSON no fd original: os callbacks de progresso rodam em
# threads do worker (ao_iniciar) e na thread principal (ao_concluir).
_TRAVA_JSON = threading.Lock()


def _indice_primeiro_posicional(argv: list[str]) -> int | None:
    """Índice do 1º argumento não-flag (pulando valores de opções).

    `pdf2md --workers 8 in.pdf out/` → pula "--workers" E "8" (valor da
    opção) até achar "in.pdf". Usado pelo default-command (FIX 5).
    `-` sozinho é posicional (ORIGEM vinda do stdin), não flag.
    """
    i = 1
    while i < len(argv):
        arg = argv[i]
        if arg.startswith("-") and arg != "-":
            if arg in _OPCOES_COM_VALOR:
                i += 2  # flag + valor
            else:
                i += 1
            continue
        return i
    return None


def _sem_markup(texto: str) -> str:
    """Remove markup Rich ([red]...[/red]) de mensagens (FIX 9).

    Aplicado apenas a mensagens geradas pelo próprio CLI (hardcoded), que
    são as únicas que podem conter tags Rich — nunca em mensagens de erro
    sanitizadas (que podem conter "[user]" legítimo).
    """
    return _RE_MARKUP_RICH.sub("", texto)


def main() -> None:
    """
    Entry point real (console script `pdf2md`).

    Default-command: quando o primeiro argumento não é um comando/subcomando
    conhecido, injeta `converter` — a forma `pdf2md <arquivo> <destino>` é a
    canônica (README + GUI BatchProcessor). O click vendored do Typer 0.26
    não suporta args de grupo + subcomandos na mesma árvore, então o
    default-command é resolvido aqui, antes do parser.

    FIX 5: flags antes do positional (`pdf2md --sobrescrever in.pdf out/`)
    quebravam com 'No such option' — as flags são REORDENADAS para depois
    do `converter` injetado.
    """
    if len(sys.argv) > 1 and sys.argv[1] not in _COMANDOS_TOPO:
        if sys.argv[1].startswith("-") and sys.argv[1] != "-":
            pos = _indice_primeiro_posicional(sys.argv)
            if pos is not None and sys.argv[pos] not in _COMANDOS_TOPO:
                # `pdf2md --sobrescrever in.pdf out/` → `converter --sobrescrever in.pdf out/`
                flags = sys.argv[1:pos]
                del sys.argv[1:pos]
                sys.argv.insert(1, "converter")
                sys.argv[2:2] = flags
        else:
            sys.argv.insert(1, "converter")
    app()


console = Console(stderr=True)


def _emitir_json(
    id_: str,
    status: str,
    erro: str | None,
    avisos: list[str] | None = None,
    fd: int | None = None,
) -> None:
    """Emite linha JSON no stdout para consumo pelo Swift bridge.

    Com `fd` (stdout original, salvo por `_silenciar_stdout_nativo`), escreve
    direto no descritor sob trava — o fd 1 está redirecionado durante a
    conversão, e threads de worker emitem concorrentemente.
    """
    linha = json.dumps(
        {"id": id_, "status": status, "erro": erro, "avisos": avisos or []},
        ensure_ascii=False,
    )
    if fd is None:
        sys.stdout.write(linha + "\n")
        sys.stdout.flush()
        return
    dados = (linha + "\n").encode("utf-8")
    with _TRAVA_JSON:
        while dados:
            escritos = os.write(fd, dados)
            dados = dados[escritos:]


def _ler_lista_stdin() -> dict[Path, str]:
    """Lê ORIGEM `-`: caminhos absolutos separados por NUL, em UTF-8.

    Devolve {Path: string recebida} na ordem de entrada. A string original é
    guardada porque o campo `id` do JSON deve ser exatamente o que chegou no
    stdin (sem normalização do pathlib).
    """
    ids: dict[Path, str] = {}
    for bruto in sys.stdin.buffer.read().split(b"\0"):
        if not bruto:
            continue
        texto = os.fsdecode(bruto)
        ids.setdefault(Path(texto), texto)
    return ids


def _callback_iniciar_json(fd: int, ids: Mapping[Path, str]) -> Callable[[Path], None]:
    """Callback `ao_iniciar` do batch: emite a linha "processando" no fd original."""
    return lambda p: _emitir_json(ids.get(p, str(p)), "processando", None, fd=fd)


def _callback_concluir_json(
    fd: int, ids: Mapping[Path, str],
) -> Callable[["ResultadoArquivo"], None]:
    """Callback `ao_concluir` do batch: emite a linha final de cada arquivo."""
    return lambda r: _emitir_json(
        ids.get(r.origem, str(r.origem)), r.status.value, r.erro, r.avisos, fd=fd,
    )


def _fmt_duracao(seg: float) -> str:
    """Formata duração legível: '8ms' (<1s), '1.2s' (<1min), '1m02s' (>=1min).

    Conversões de texto levam milissegundos; '.1f' arredondava tudo <0.05s
    para '0.0s'. Sub-segundo é exibido em ms.
    """
    if seg < 1:
        return f"{seg * 1000:.0f}ms"
    if seg < 60:
        return f"{seg:.1f}s"
    minutos, segundos = divmod(int(round(seg)), 60)
    return f"{minutos}m{segundos:02d}s"


@contextmanager
def _silenciar_stdout_nativo() -> Iterator[int]:
    """
    Redireciona o fd 1 (stdout) para o fd 2 (stderr) no nível do OS.

    Bibliotecas C (MuPDF, via pymupdf4llm) escrevem mensagens direto no fd 1
    — `contextlib.redirect_stdout` não as captura, pois trocam só `sys.stdout`.
    Sem isto, o ruído ("Using Tesseract for OCR processing") contamina o
    protocolo JSON consumido pelo bridge Swift. Usado só ao redor da conversão;
    thread-safe na fronteira (as threads do batch são unidas antes de restaurar).

    Yield: o descritor do stdout original (cópia de fd 1), por onde o JSON
    em streaming ainda chega ao consumidor.
    """
    fd_salvo = os.dup(1)
    try:
        os.dup2(2, 1)
        yield fd_salvo
    finally:
        os.dup2(fd_salvo, 1)
        os.close(fd_salvo)


def _requer_ocr(
    origem: Path | list[Path], imagens: ModoImagem = ModoImagem.transcrever,
) -> bool:
    """
    True se a entrada pode exigir OCR (imagens ou PDFs).
    Documentos Word (.doc/.docx) usam mammoth/textutil e dispensam Tesseract,
    exceto .docx com `--imagens ambos`: lá cada imagem recebe OCR como alt-text.
    .doc não entra: o caminho textutil não extrai imagens.
    Lista (entrada `-`): basta um arquivo com extensão de OCR.
    """
    ocr_exts = EXTENSOES_IMAGEM | EXTENSOES_PDF
    if imagens == ModoImagem.ambos:
        ocr_exts = ocr_exts | {".docx"}
    if isinstance(origem, list):
        return any(p.is_file() and p.suffix.lower() in ocr_exts for p in origem)
    if origem.is_file():
        return origem.suffix.lower() in ocr_exts
    if origem.is_dir():
        return any(p.suffix.lower() in ocr_exts for p in origem.iterdir())
    return False


@app.command()
def converter(
    origem: Path = typer.Argument(..., help="Arquivo ou diretório de entrada"),
    destino: Path = typer.Argument(
        Path("."), help="Diretório de saída (padrão: diretório atual)"
    ),
    # min=1: ThreadPoolExecutor(max_workers=0) levantava ValueError com traceback.
    workers: int = typer.Option(4, "--workers", "-w", min=1, help="Threads paralelas"),
    sobrescrever: bool = typer.Option(False, "--sobrescrever", help="Sobrescreve MDs existentes"),
    vault: Path | None = typer.Option(None, "--vault", help="Path do Obsidian vault. Output vai direto para vault/"),
    obsidian: bool = typer.Option(False, "--obsidian", help="Adiciona frontmatter Obsidian ao MD"),
    llm_fallback: bool = typer.Option(
        False, "--llm-fallback",
        help="Usa LLM (Ollama local ou provedor remoto) para melhorar qualidade quando "
             "detectados problemas. Configura via PDF2MD_LLM_URL / PDF2MD_LLM_MODEL.",
    ),
    usar_llm: bool = typer.Option(
        False, "--llm",
        help="Sempre aplica LLM para pós-processamento (independente da qualidade). "
             "Mais lento que --llm-fallback.",
    ),
    json_output: bool = typer.Option(False, "--json", hidden=True, help="Output em JSON por linha"),
    ignorar_margens: float = typer.Option(
        0.0, "--ignorar-margens",
        help="Ignora cabeçalho e rodapé de páginas PDF (percentual da altura). "
             "Ex: 5 ignora 5% do topo e 5% do rodapé. Padrão: 0 (desativado).",
    ),
    llm_url: str | None = typer.Option(
        None, "--llm-url",
        help="URL base da API LLM (precedência: flag > PDF2MD_LLM_URL > Ollama local).",
    ),
    llm_modelo: str | None = typer.Option(
        None, "--llm-modelo",
        help="Modelo LLM (precedência: flag > PDF2MD_LLM_MODEL > llama3.2-vision).",
    ),
    imagens: ModoImagem = typer.Option(
        ModoImagem.transcrever, "--imagens",
        help="Política de imagens embutidas em PDF e DOCX: transcrever (OCR de scans), "
             "extrair (salva assets + links), ambos (extrai + OCR no alt-text) ou "
             "ignorar (descarta sem OCR). Só se aplica a PDF e DOCX.",
    ),
    assets_dir: Path | None = typer.Option(
        None, "--assets-dir",
        help="Diretório dos assets extraídos (--imagens extrair|ambos). "
             "Default: '<stem>_assets/' ao lado do .md; com --obsidian, vault/attachments/.",
    ),
) -> None:
    """
    Converte PDFs, imagens, Word, PPTX e planilhas em Markdown.
    --vault implica --obsidian automaticamente.

    ORIGEM `-`: lista de arquivos no stdin, separados por NUL (protocolo da
    GUI). Com --json, cada arquivo emite "processando" ao começar e uma linha
    final ao concluir, em tempo real.
    """
    # Lazy imports: só carrega deps pesadas quando o comando é executado
    from core.batch import StatusArquivo, batch_convert
    from core.image_converter import verificar_tesseract
    from core.llm_enhancer import ConfigLLM

    # ORIGEM `-` → lista vinda do stdin (ids preservam a string recebida)
    entrada: Path | list[Path] = origem
    ids: dict[Path, str] = {}
    if str(origem) == "-":
        ids = _ler_lista_stdin()
        if not ids:
            console.print("[red]Nenhum arquivo recebido pela entrada padrão (-).[/red]")
            raise typer.Exit(code=1)
        entrada = list(ids)

    # Validação de segurança
    try:
        for arq in entrada if isinstance(entrada, list) else [entrada]:
            validar_path_seguro(arq)
        if vault:
            validar_path_seguro(vault)
        validar_path_seguro(destino)
    except ValueError as exc:
        console.print(f"[red]Erro de validação: {exc}[/red]")
        raise typer.Exit(code=1) from exc

    # Verifica Tesseract apenas se a entrada puder exigir OCR.
    # (.doc usa textutil e .docx usa mammoth; nenhum dos dois exige Tesseract,
    # exceto .docx com `--imagens ambos`, que faz OCR de cada imagem.)
    if _requer_ocr(entrada, imagens) and not verificar_tesseract():
        msg = (
            "[red]Tesseract não encontrado. "
            "Instale: brew install tesseract tesseract-lang[/red]"
        )
        console.print(msg)
        if json_output:
            # FIX 9: o campo `erro` do JSON não pode carregar markup Rich
            # ([red]...[/red]) — o bridge Swift exibe a string crua.
            _emitir_json(str(origem), StatusArquivo.ERRO.value, _sem_markup(msg))
        raise typer.Exit(code=1)

    # Vault implica obsidian
    if vault is not None:
        obsidian = True

    # Config LLM: flag > env > default — None preserva o comportamento env-only
    llm_config = (
        ConfigLLM(url=llm_url, modelo=llm_modelo)
        if (llm_url or llm_modelo) else None
    )

    inicio = time.perf_counter()
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            console=console,
            transient=True,
        ) as progress:
            task = progress.add_task("Convertendo...", total=None)
            # Silencia o ruído nativo do MuPDF no stdout durante a conversão.
            # O JSON em streaming vai pelo fd original (yield), não por sys.stdout.
            with _silenciar_stdout_nativo() as fd_stdout:
                resultados = batch_convert(
                    origem=entrada,
                    destino=destino,
                    workers=workers,
                    sobrescrever=sobrescrever,
                    vault=vault,
                    obsidian=obsidian,
                    usar_llm=usar_llm,
                    llm_fallback=llm_fallback,
                    ignorar_margens=ignorar_margens,
                    llm_config=llm_config,
                    modo_imagem=imagens,
                    assets_dir=assets_dir,
                    ao_iniciar=_callback_iniciar_json(fd_stdout, ids) if json_output else None,
                    ao_concluir=_callback_concluir_json(fd_stdout, ids) if json_output else None,
                )
            progress.update(task, total=len(resultados), completed=len(resultados))
    except (FileNotFoundError, NotADirectoryError) as exc:
        console.print(f"[red]Erro: {exc}[/red]")
        raise typer.Exit(code=1) from exc
    tempo_total = time.perf_counter() - inicio

    # JSON já foi emitido linha a linha pelos callbacks (ver batch_convert)
    if json_output:
        return
    # Tabela de resultados
    table = Table(title="Resultados da conversão")
    table.add_column("Origem", style="cyan")
    table.add_column("Status", style="green")
    table.add_column("Destino", style="magenta")
    table.add_column("Erro/Aviso", style="red")

    for res in resultados:
        tem_avisos = bool(res.avisos)
        if res.status == StatusArquivo.CONCLUIDO and tem_avisos:
            status_color = "yellow"
            status_label = "concluido⚠"  # ⚠ unicode
        else:
            status_color = {
                StatusArquivo.CONCLUIDO: "green",
                StatusArquivo.ERRO: "red",
                StatusArquivo.IGNORADO: "yellow",
                StatusArquivo.AGUARDANDO: "dim",
                StatusArquivo.PROCESSANDO: "blue",
            }.get(res.status, "white")
            status_label = res.status.value

        nota = res.erro or (res.avisos[0] if res.avisos else "—")

        table.add_row(
            res.origem.name,
            f"[{status_color}]{status_label}[/{status_color}]",
            res.destino.name if res.destino else "—",
            nota,
        )

    console.print(table)

    total = len(resultados)
    sucessos = sum(1 for r in resultados if r.status == StatusArquivo.CONCLUIDO and not r.avisos)
    com_aviso = sum(1 for r in resultados if r.status == StatusArquivo.CONCLUIDO and r.avisos)
    erros = sum(1 for r in resultados if r.status == StatusArquivo.ERRO)
    ignorados = sum(1 for r in resultados if r.status == StatusArquivo.IGNORADO)

    partes = [
        f"\n[bold]Total:[/bold] {total}",
        f"[green]OK:[/green] {sucessos}",
    ]
    if com_aviso:
        partes.append(f"[yellow]Com aviso:[/yellow] {com_aviso}")
    partes += [
        f"[red]Erros:[/red] {erros}",
        f"[yellow]Ignorados:[/yellow] {ignorados}",
        f"[blue]Tempo:[/blue] {_fmt_duracao(tempo_total)}",
    ]
    console.print(" | ".join(partes))

    # Lista avisos completos após a tabela
    arquivos_com_aviso = [r for r in resultados if r.avisos]
    if arquivos_com_aviso:
        console.print("\n[yellow bold]⚠ Avisos de qualidade:[/yellow bold]")
        for res in arquivos_com_aviso:
            console.print(f"  [cyan]{res.origem.name}[/cyan]")
            for aviso in res.avisos:
                console.print(f"    [yellow]• {aviso}[/yellow]")


# ── Subcomandos `llm modelos` / `llm testar` (diagnóstico + GUI) ─────────────
# A GUI consome --json para popular o picker de modelos e o indicador de
# status. A API key NUNCA é aceita via argv (CWE-522) — vem do environment,
# que o bridge Swift injeta em processo.environment (nunca em ps aux).

llm_app = typer.Typer(
    name="llm",
    help="Diagnóstico do LLM: lista modelos e testa conexão.",
    add_completion=False,
)


@llm_app.command("modelos")
def llm_modelos(
    json_output: bool = typer.Option(False, "--json", hidden=True, help="Output em JSON no stdout"),
) -> None:
    """
    Lista os modelos disponíveis no endpoint configurado.

    URL/key vêm do environment (PDF2MD_LLM_URL / PDF2MD_LLM_KEY) ou do
    default Ollama local. Falha retorna {"ok": false} com exit 0 — sem
    traceback (o JSON é o contrato com a GUI, exit!=0 quebraria o parse).
    """
    from core.llm_enhancer import listar_modelos

    modelos, erro = listar_modelos()

    if json_output:
        if modelos is None:
            linha = json.dumps({"ok": False, "modelos": [], "erro": erro}, ensure_ascii=False)
        else:
            linha = json.dumps({"ok": True, "modelos": modelos}, ensure_ascii=False)
        sys.stdout.write(linha + "\n")
        sys.stdout.flush()
        return

    if modelos is None:
        console.print(f"[red]Falha ao listar modelos: {erro}[/red]")
        console.print("[yellow]Dica: verifique PDF2MD_LLM_URL e se o servidor está rodando.[/yellow]")
        raise typer.Exit(code=1)

    if not modelos:
        console.print("[yellow]Nenhum modelo retornado pelo endpoint.[/yellow]")
        return

    table = Table(title="Modelos disponíveis")
    table.add_column("Modelo", style="cyan")
    table.add_column("Visão", style="green")
    for m in modelos:
        visao = "sim" if m["visao"] is True else ("não" if m["visao"] is False else "desconhecido")
        table.add_row(m["id"], visao)
    console.print(table)


@llm_app.command("testar")
def llm_testar(
    json_output: bool = typer.Option(False, "--json", hidden=True, help="Output em JSON no stdout"),
) -> None:
    """
    Testa a conexão com o endpoint configurado (sem cache).

    Mede latência de GET /models. Falha retorna {"ok": false} com exit 0 —
    a GUI usa o campo `erro` para o aviso (mensagem segura, CWE-209).
    """
    from core.llm_enhancer import testar

    resultado = testar()

    if json_output:
        sys.stdout.write(json.dumps(resultado, ensure_ascii=False) + "\n")
        sys.stdout.flush()
        return

    if resultado["ok"]:
        console.print(f"[green]Conectado ({resultado['latencia_ms']}ms)[/green]")
    else:
        console.print(f"[red]Inacessível: {resultado['erro']}[/red]")
        raise typer.Exit(code=1)


app.add_typer(llm_app)

# Guard no FINAL do módulo: no binário PyInstaller o módulo é executado como
# `__main__` — se este guard rodasse no topo, chamaria app() antes do
# add_typer(llm_app) e o app congelado ficaria sem comandos.
if __name__ == "__main__":
    main()
