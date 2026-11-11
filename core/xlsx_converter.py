"""
Converte planilhas (.xlsx, .csv) em Markdown.

Estratégia:
- .xlsx → openpyxl: cada sheet vira tabela Markdown com nome como heading
- .csv  → stdlib csv: uma tabela Markdown (sem deps extras)

Nota: .xls (formato legado) não suportado — requer xlrd e o formato está
obsoleto desde Excel 2007. Usuários devem converter para .xlsx primeiro.
"""
import csv
import io
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from core.utils import _linha_tabela_md, _sanitizar_celula_md, _validar_existencia

# Encodings tentados em cascade para CSV — latin-1 nunca falha (mapeia 256 bytes).
# utf-8-sig cobre UTF-8 com e sem BOM.
_CSV_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")


def planilha_to_md(path: Path) -> str:
    """
    Converte planilha (.xlsx ou .csv) em Markdown.

    Dispatcha para o conversor correto pela extensão.

    Args:
        path: Caminho para o arquivo de planilha.

    Returns:
        String Markdown com conteúdo tabular extraído.

    Raises:
        FileNotFoundError: Se path não existe.
        ValueError: Se extensão não é suportada.
    """
    _validar_existencia(path)

    sufixo = path.suffix.lower()
    if sufixo == ".xlsx":
        return _xlsx_para_md(path)
    elif sufixo == ".csv":
        return _csv_para_md(path)
    else:
        raise ValueError(f"Extensão não suportada por planilha_to_md: {sufixo}")


def _xlsx_para_md(path: Path) -> str:
    """Converte .xlsx em Markdown usando openpyxl."""
    import openpyxl  # lazy import — evita custo no startup

    try:
        wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    except Exception as exc:
        raise RuntimeError(
            "Falha ao abrir XLSX — arquivo corrompido ou formato inválido"
        ) from exc

    # Cada aba vira um bloco "## Nome\n\n<tabela>"; blocos separados por "\n\n".
    # Linhas da tabela usam "\n" simples — linha em branco no meio quebra a
    # renderização em GFM/Obsidian.
    partes: list[str] = []

    try:
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]

            # FIX 6: uma passada em streaming (read_only=True) — sem
            # list(iter_rows(values_only=True)), que materializaria 1M linhas.
            # _tabela_md pula linhas vazias no topo, então a primeira linha
            # com conteúdo vira o header; sheet sem conteúdo devolve "".
            tabela = _tabela_md(
                [_celula_str(c) for c in row] for row in ws.iter_rows(values_only=True)
            )
            if not tabela:
                continue  # sheet sem nenhuma célula com conteúdo
            partes.append(f"## {sheet_name}\n\n" + tabela)
    finally:
        wb.close()

    return "\n\n".join(partes)


def _tabela_md(linhas: Iterable[list[str]]) -> str:
    """Monta tabela Markdown GFM com largura uniforme a partir de células sanitizadas.

    Regras:
    - Linhas totalmente vazias são descartadas; a 1ª restante é o cabeçalho.
    - Colunas vazias à direita de cada linha saem (openpyxl costuma padear a
      planilha com colunas vazias em todas as linhas).
    - Largura = maior número de células entre cabeçalho e corpo. Linhas mais
      curtas recebem células vazias à direita; a tabela fica válida.

    Retorna "" se não houver nenhuma célula com conteúdo.
    """
    validas: list[list[str]] = []
    for celulas in linhas:
        aparadas = list(celulas)
        while aparadas and not aparadas[-1]:
            aparadas.pop()
        if aparadas:
            validas.append(aparadas)

    if not validas:
        return ""

    largura = max(len(linha) for linha in validas)
    tabela = [
        _linha_tabela_md(linha + [""] * (largura - len(linha))) for linha in validas
    ]
    # Separador entre cabeçalho e corpo, com a mesma largura.
    tabela.insert(1, _linha_tabela_md(["---"] * largura))
    return "\n".join(tabela)


def _csv_para_md(path: Path) -> str:
    """Converte .csv em Markdown usando stdlib csv. Lê bytes uma vez, decode in-memory."""
    dados = path.read_bytes()

    rows = None
    for enc in _CSV_ENCODINGS:
        try:
            texto = dados.decode(enc)
        except UnicodeDecodeError:
            continue
        # StringIO com newline="" (não splitlines): célula entre aspas com
        # quebra de linha chega inteira ao csv.reader — splitlines colava
        # as palavras ("linha1\nlinha2" virava "linha1linha2").
        rows = list(csv.reader(io.StringIO(texto, newline="")))
        break

    if not rows:
        return ""

    return _tabela_md([_celula_str(c) for c in row] for row in rows)


def _celula_str(valor: Any) -> str:
    """Normaliza valor de célula para string Markdown segura."""
    if valor is None:
        return ""
    return _sanitizar_celula_md(str(valor))
