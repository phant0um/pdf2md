#!/usr/bin/env bash
#
# build_app.sh — Empacota o pdf2md como .app + .dmg para macOS (Apple Silicon).
#
# Pipeline:
#   1. PyInstaller: core/cli.py → binário one-file `pdf2md`
#   2. swiftc: gui/PDF2MD/*.swift → executável `PDF2MD` (sem Xcode, só CLT)
#   3. Monta PDF2MD.app (Info.plist + ícone + binário Python embarcado)
#   4. Codesign com identidade estável (Apple Development); ad-hoc só como fallback
#   5. hdiutil → PDF2MD-v<versão>.dmg
#
# Sem paths hardcoded: tudo derivado do local do script, do pyproject e do
# ambiente Python ativo. Pré-requisito: `uv sync --extra dev` (pyinstaller +
# deps em .venv/) e Command Line Tools (swiftc, hdiutil, codesign).
#
# Uso: ./scripts/build_app.sh
#      PDF2MD_SDK=<caminho.sdk> ./scripts/build_app.sh   (força o SDK do swiftc)
set -euo pipefail

# ── Paths base (derivados, nunca hardcoded) ─────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RAIZ="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${RAIZ}"

DIST="${RAIZ}/dist"
BUILD="${RAIZ}/build"
APP="${DIST}/PDF2MD.app"

# ── Versão: fonte única é o pyproject.toml ──────────────────────────────────
# Ordem: venv ativo → .venv/ do projeto (uv sync) → python3 do sistema.
# `${VIRTUAL_ENV:-}`: com `set -u`, a variável ausente abortava o script.
PYTHON_BIN="python3"
if [[ -x "${VIRTUAL_ENV:-}/bin/python" ]]; then
    PYTHON_BIN="${VIRTUAL_ENV}/bin/python"
elif [[ -x "${RAIZ}/.venv/bin/python" ]]; then
    PYTHON_BIN="${RAIZ}/.venv/bin/python"
fi
VERSAO="$("${PYTHON_BIN}" -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
# Número de build incremental e reproduzível (contagem de commits)
BUILD_NUM="$(git rev-list --count HEAD 2>/dev/null || echo 1)"

echo "▶ pdf2md build — versão ${VERSAO} (build ${BUILD_NUM})"

# ── SDK do swiftc ───────────────────────────────────────────────────────────
# Com só Command Line Tools, o SDK padrão pode declarar as macros do SwiftUI
# (@State etc.) sem o plugin SwiftUIMacros do compilador: o swiftc falha em
# código inalterado. SDKs versionados no mesmo CLT (ex.: MacOSX26.5.sdk)
# compilam a GUI. Escolhe-se o primeiro SDK que passa numa sonda de typecheck,
# ANTES do PyInstaller (minutos), para falhar cedo.
SONDA_DIR="${BUILD}/sonda-sdk"
mkdir -p "${SONDA_DIR}"
SONDA="${SONDA_DIR}/Sonda.swift"
cat > "${SONDA}" <<'SWIFT'
import SwiftUI
struct Sonda: View {
    @State private var n = 0
    var body: some View { Text("\(n)") }
}
SWIFT

SDK_PADRAO="$(xcrun --show-sdk-path)"
CANDIDATOS=()
if [[ -n "${PDF2MD_SDK:-}" ]]; then
    # Override explícito: sem fallback, se falhar o build para
    CANDIDATOS=("${PDF2MD_SDK}")
else
    CANDIDATOS=("${SDK_PADRAO}")
    # SDKs versionados vizinhos ao padrão, do mais novo para o mais antigo
    VERSIONADOS="$(ls -d "$(dirname "${SDK_PADRAO}")"/MacOSX[0-9]*.sdk 2>/dev/null | sort -V -r || true)"
    while IFS= read -r s; do
        [[ -n "${s}" ]] && CANDIDATOS+=("${s}")
    done <<< "${VERSIONADOS}"
fi

SWIFT_SDK=""
for c in "${CANDIDATOS[@]}"; do
    if swiftc -parse-as-library -typecheck -target arm64-apple-macos13 \
        -sdk "${c}" "${SONDA}" >/dev/null 2>&1; then
        SWIFT_SDK="${c}"
        break
    fi
done

if [[ -z "${SWIFT_SDK}" ]]; then
    if [[ -n "${PDF2MD_SDK:-}" ]]; then
        echo "✗ PDF2MD_SDK=${PDF2MD_SDK} não compila a GUI SwiftUI" >&2
    else
        echo "✗ nenhum SDK instalado compila a GUI SwiftUI" >&2
        echo "  Instale o Xcode, ou atualize o CLT (xcode-select --install)," >&2
        echo "  ou aponte um SDK com PDF2MD_SDK=<caminho.sdk>" >&2
    fi
    exit 1
fi

echo "▶ SDK do swiftc: $(basename "${SWIFT_SDK}")"
# Resolve symlinks (MacOSX.sdk → MacOSX27.2.sdk) para comparar com o padrão
if [[ "$(cd -P "${SWIFT_SDK}" && pwd -P)" != "$(cd -P "${SDK_PADRAO}" && pwd -P)" ]]; then
    echo "  ⚠ SDK padrão sem suporte a macros do SwiftUI — usando $(basename "${SWIFT_SDK}")" >&2
fi

# ── 1. PyInstaller: binário Python one-file ─────────────────────────────────
# pymupdf precisa dos resources de layout embarcados (senão FileNotFoundError
# em runtime no binário congelado).
PYMUPDF_RES="$("${PYTHON_BIN}" -c "import pymupdf, os; print(os.path.join(os.path.dirname(pymupdf.__file__), 'layout', 'resources'))")"
if [[ ! -d "${PYMUPDF_RES}" ]]; then
    echo "✗ resources do pymupdf não encontrados: ${PYMUPDF_RES}" >&2
    exit 1
fi

echo "▶ [1/5] PyInstaller → dist/pdf2md"
"${PYTHON_BIN}" -m PyInstaller core/cli.py \
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

# ── 2. swiftc: executável da GUI ────────────────────────────────────────────
echo "▶ [2/5] swiftc → PDF2MD (executável SwiftUI)"
rm -rf "${APP}"
mkdir -p "${APP}/Contents/MacOS" "${APP}/Contents/Resources"

swiftc -parse-as-library -O \
    -target arm64-apple-macos13 \
    -sdk "${SWIFT_SDK}" \
    gui/PDF2MD/PDF2MDApp.swift \
    gui/PDF2MD/ContentView.swift \
    gui/PDF2MD/BatchProcessor.swift \
    gui/PDF2MD/LLMConfig.swift \
    gui/PDF2MD/KeychainHelper.swift \
    gui/PDF2MD/ProcessRunner.swift \
    gui/PDF2MD/SettingsView.swift \
    -o "${APP}/Contents/MacOS/PDF2MD"

# ── 3. Monta o bundle .app ──────────────────────────────────────────────────
echo "▶ [3/5] Montando PDF2MD.app"
cp "${DIST}/pdf2md" "${APP}/Contents/Resources/pdf2md"
cp "gui/PDF2MD/Assets.xcassets/AppIcon.appiconset/AppIcon.icns" \
   "${APP}/Contents/Resources/AppIcon.icns"
# Avisos de licença viajam com o binário (atribuição AGPL/terceiros)
cp LICENSE "${APP}/Contents/Resources/LICENSE"
cp THIRD-PARTY-LICENSES.md "${APP}/Contents/Resources/THIRD-PARTY-LICENSES.md"

cat > "${APP}/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>pdf2md</string>
    <key>CFBundleDisplayName</key><string>pdf2md</string>
    <key>CFBundleIdentifier</key><string>com.mchlcs.pdf2md</string>
    <key>CFBundleVersion</key><string>${BUILD_NUM}</string>
    <key>CFBundleShortVersionString</key><string>${VERSAO}</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleExecutable</key><string>PDF2MD</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>LSMinimumSystemVersion</key><string>13.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSSupportsAutomaticGraphicsSwitching</key><true/>
    <key>NSPrincipalClass</key><string>NSApplication</string>
</dict>
</plist>
PLIST

# ── 4. Codesign com identidade estável ─────────────────────────────────────
# A ACL do item de Keychain (API key do LLM) é amarrada à assinatura do app.
# Ad-hoc (-) muda a cada build → macOS pede a senha de login ao abrir o app.
# Identidade estável mantém o designated requirement entre builds, e o
# "Permitir Sempre" do usuário persiste (ver ADR-0007).
# Ordem: $PDF2MD_SIGN_IDENTITY → 1ª "Apple Development" válida → ad-hoc.
# Sem notarização: Gatekeeper ainda exige "botão direito → Abrir" em outra Mac.
IDENTIDADE="${PDF2MD_SIGN_IDENTITY:-}"
if [[ -z "${IDENTIDADE}" ]]; then
    IDENTIDADE="$(security find-identity -v -p codesigning 2>/dev/null \
        | awk '/"Apple Development:/ {print $2; exit}')"
fi
if [[ -n "${IDENTIDADE}" ]]; then
    echo "▶ [4/5] Codesign (identidade ${IDENTIDADE})"
    codesign --force --deep --sign "${IDENTIDADE}" "${APP}" || {
        # errSecInternalComponent = codesign sem acesso à chave privada
        # (keychain bloqueado ou sessão sem GUI para o diálogo de permissão).
        echo "✗ codesign falhou. Rode o build num Terminal da sessão gráfica e" >&2
        echo "  clique \"Permitir Sempre\" no diálogo da chave, ou desbloqueie:" >&2
        echo "  security unlock-keychain ~/Library/Keychains/login.keychain-db" >&2
        exit 1
    }
else
    echo "▶ [4/5] Codesign ad-hoc"
    echo "  ⚠ sem identidade estável: o macOS pedirá a senha do Keychain após cada rebuild" >&2
    codesign --force --deep --sign - "${APP}"
fi
codesign --verify --deep --strict "${APP}" && echo "  ✓ assinatura válida"

# ── 5. DMG ──────────────────────────────────────────────────────────────────
# NÃO usar `hdiutil create -srcfolder` com o symlink /Applications: o auto-size
# segue o symlink e estoura a imagem ("no space"). Em vez disso: imagem RW com
# tamanho EXPLÍCITO → monta → copia app + symlink → desmonta → comprime (UDZO).
echo "▶ [5/5] hdiutil → DMG"
VOLNAME="pdf2md ${VERSAO}"
DMG_RW="${BUILD}/pdf2md-rw.dmg"
DMG_OUT="${DIST}/PDF2MD-v${VERSAO}.dmg"

# Limpa montagens/artefatos de execuções anteriores (volumes pdf2md stale)
for v in /Volumes/pdf2md*; do
    [[ -d "${v}" ]] && hdiutil detach "${v}" >/dev/null 2>&1 || true
done
rm -f "${DMG_RW}" "${DMG_OUT}"

# Tamanho = app + 40MB de margem
TAM_MB="$(du -sm "${APP}" | awk '{print $1 + 40}')"
# Imagem RW vazia: -size + -fs (SEM -format, que exigiria -srcfolder).
hdiutil create -volname "${VOLNAME}" -fs HFS+ \
    -size "${TAM_MB}m" -ov "${DMG_RW}" -quiet

# Monta em /Volumes/<volname>, captura o device node (detach por device +
# -force é confiável; detach por mountpoint falha com "resource busy" logo
# após o cp).
MONTADO="/Volumes/${VOLNAME}"
DEV="$(hdiutil attach "${DMG_RW}" -nobrowse -noverify | grep -E '^/dev/' | head -1 | awk '{print $1}')"
cp -RP "${APP}" "${MONTADO}/"
ln -s /Applications "${MONTADO}/Applications"
sync
hdiutil detach "${DEV}" -force -quiet

# Converte read-write → comprimido (UDZO) — o .dmg final de distribuição
hdiutil convert "${DMG_RW}" -format UDZO -o "${DMG_OUT}" -quiet
rm -f "${DMG_RW}"

echo ""
echo "✓ Build completo:"
echo "  .app → ${APP}"
echo "  .dmg → ${DMG_OUT}"
echo ""
echo "Próximo: gh release create v${VERSAO} \"${DMG_OUT}\""
