#!/usr/bin/env bash
#
# pyinstaller_cli.sh — Invocação única do PyInstaller que gera o binário `pdf2md`.
#
# Existe para ter UMA fonte das flags: o CI (.github/workflows/ci.yml) e o
# scripts/build_app.sh chamam este script. Flag adicionada em um só lugar
# divergiria do binário distribuído e o smoke do CI testaria outro binário.
#
# Uso: scripts/pyinstaller_cli.sh <distpath> <buildpath>
#      PYTHON_BIN=<python> scripts/pyinstaller_cli.sh dist build
#
# Sem PYTHON_BIN usa `python` do PATH (no CI, `uv run` coloca o python do venv
# primeiro no PATH).
set -euo pipefail

if [[ $# -ne 2 ]]; then
    echo "uso: $(basename "$0") <distpath> <buildpath>" >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${RAIZ}"

DIST="$1"
BUILD="$2"
PY="${PYTHON_BIN:-python}"

# pymupdf precisa dos resources de layout embarcados (senão FileNotFoundError
# em runtime no binário congelado).
PYMUPDF_RES="$("${PY}" -c "import pymupdf, os; print(os.path.join(os.path.dirname(pymupdf.__file__), 'layout', 'resources'))")"
if [[ ! -d "${PYMUPDF_RES}" ]]; then
    echo "✗ resources do pymupdf não encontrados: ${PYMUPDF_RES}" >&2
    exit 1
fi

"${PY}" -m PyInstaller core/cli.py \
    --onefile \
    --name pdf2md \
    --add-data "${PYMUPDF_RES}:pymupdf/layout/resources" \
    --collect-submodules pymupdf4llm \
    --collect-submodules mammoth \
    --hidden-import pillow_heif \
    --distpath "${DIST}" \
    --workpath "${BUILD}/pyinstaller" \
    --specpath "${BUILD}" \
    --noconfirm \
    --clean

[[ -f "${DIST}/pdf2md" ]] || { echo "✗ binário pdf2md não gerado" >&2; exit 1; }
