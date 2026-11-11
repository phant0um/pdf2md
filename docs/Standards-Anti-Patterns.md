# Standards & Anti-Patterns — pdf2md

> Catálogo de decisões técnicas e armadilhas conhecidas.
> Atualizado a cada ciclo. Leia **antes** de adicionar nova dependência externa ou
> modificar o pipeline de empacotamento.

---

## 1. Subprocess de binário de sistema em app PyInstaller

### Problema
Binário PyInstaller one-file tem **PATH mínimo** — sem `/opt/homebrew/bin/`,
sem `/usr/local/bin/`. Chamar `subprocess.run(["tesseract", ...])` ou
`subprocess.run(["antiword", ...])` falha silenciosamente com `FileNotFoundError`
no app empacotado, mesmo que o binário esteja instalado no sistema.

**Custo real:** Tesseract — v0.1.0 quebrado. Antiword — v0.2.0 quebrado.
Mesma causa-raiz, dois releases.

### Regra
**Nunca** chamar binário de sistema por nome literal em código que será congelado
pelo PyInstaller. Sempre usar um resolver com fallback explícito.

### Padrão canônico

```python
_CAMINHOS_MACOS = [
    "/opt/homebrew/bin/<bin>",   # Homebrew Apple Silicon
    "/usr/local/bin/<bin>",      # Homebrew Intel
    "/usr/bin/<bin>",            # instalação manual
]

@lru_cache(maxsize=None)
def _resolver_<bin>() -> str:
    """Retorna path absoluto do binário ou nome literal como fallback."""
    import shutil
    for caminho in _CAMINHOS_MACOS:
        if Path(caminho).exists():
            return caminho
    encontrado = shutil.which("<bin>")
    if encontrado:
        return encontrado
    return "<bin>"  # fallback — falhará com mensagem clara se não instalado
```

### Checklist ao adicionar nova dep externa via subprocess

```bash
# Antes de empacotar: verificar que o resolver existe
grep -n "subprocess.run" core/
# Se novo binário: copiar o padrão _resolver_<bin>() acima
# Validar no binário CONGELADO (não no dev env) com PATH mínimo:
# PATH=/usr/bin:/bin ./dist/pdf2md arquivo.pptx saida/
```

### Instâncias atuais

| Binário | Resolver | Arquivo |
|---------|----------|---------|
| `tesseract` | `_configurar_tesseract_cmd()` | `core/image_converter.py` |
| `textutil` | path absoluto fixo `/usr/bin/textutil` (nativo do macOS, sem resolver) | `core/doc_converter.py` |

---

## 2. hdiutil — criação de DMG com binário grande

### Problema
`hdiutil create -srcfolder dist/PDF2MD.app -format UDZO PDF2MD.dmg` falha com
"no space left on device" quando o app tem binário grande (>80MB) e o symlink
`/Applications` está dentro da pasta fonte — o hdiutil segue o symlink e calcula
espaço errado.

### Anti-patterns

```bash
# ❌ auto-size falha com symlink /Applications
hdiutil create -srcfolder pasta_com_symlink -format UDZO out.dmg

# ❌ -format exige -srcfolder (não funciona com imagem RW vazia)
hdiutil create -size 150m -format UDZO -fs HFS+ out.dmg
```

### Padrão canônico (usado em `scripts/build_app.sh`)

```bash
# 1. Calcular tamanho real + margem
TAMANHO_MB=$(du -sm "dist/PDF2MD.app" | awk '{print $1}')
TAMANHO_DMG=$((TAMANHO_MB + 20))  # margem de 20MB

# 2. Criar imagem RW de tamanho explícito (sem -srcfolder)
hdiutil create -size "${TAMANHO_DMG}m" -fs HFS+ -volname "PDF2MD" rw_tmp.dmg

# 3. Montar e copiar
DEVICE=$(hdiutil attach -readwrite -noverify rw_tmp.dmg | awk 'NR==1{print $1}')
cp -r "dist/PDF2MD.app" "/Volumes/PDF2MD/"
ln -s /Applications "/Volumes/PDF2MD/Applications"

# 4. Detach por DEVICE NODE com -force (não por mountpoint — dá "resource busy")
hdiutil detach "$DEVICE" -force

# 5. Converter para UDZO (comprimido, somente-leitura)
hdiutil convert rw_tmp.dmg -format UDZO -o "PDF2MD-vX.Y.Z.dmg"
rm rw_tmp.dmg
```

---

## 3. ThreadPoolExecutor vs ProcessPoolExecutor em PyInstaller

### Problema
`ProcessPoolExecutor` usa `spawn` para criar processos filhos. Em binário
PyInstaller one-file, o processo filho re-executa o binário congelado com
flags adicionais que o Typer/Click rejeita com erro de argumento inválido.

### Regra
**Sempre** usar `ThreadPoolExecutor` em código que roda dentro do app PyInstaller.

```python
# ✅
from concurrent.futures import ThreadPoolExecutor
with ThreadPoolExecutor(max_workers=workers) as executor:
    ...

# ❌ quebra PyInstaller
from concurrent.futures import ProcessPoolExecutor
```

**Por que funciona:** PyMuPDF (fitz) e Tesseract liberam o GIL em chamadas C —
threads têm paralelismo real mesmo com o GIL Python.

---

## 4. Decodificação de saída de binário externo

### Problema
`subprocess.run(["antiword", arquivo], text=True)` decodifica a saída usando o
locale do sistema (UTF-8 no macOS). Antiword emite Latin-1. Resultado:
`UnicodeDecodeError` em documentos com acentos PT-BR.

**Mesma classe:** `pytesseract` decodifica stderr do Tesseract como UTF-8 →
`UnicodeDecodeError` em imagens em branco (stderr não-UTF8 do Tesseract).

### Padrão canônico

```python
# ✅ capturar bytes, decodificar defensivamente
resultado = subprocess.run(
    [_resolver_antiword(), arquivo],
    capture_output=True,          # sem text=True
    timeout=30,
)
texto = _decodificar_bytes(resultado.stdout)

def _decodificar_bytes(dados: bytes) -> str:
    """utf-8 → cp1252 → latin-1 (latin-1 nunca falha)."""
    for enc in ("utf-8", "cp1252", "latin-1"):
        try:
            return dados.decode(enc)
        except UnicodeDecodeError:
            continue
    return dados.decode("latin-1", errors="replace")
```

---

## 5. stdout fd-nativo vs sys.stdout (PyMuPDF / C libs)

### Problema
`contextlib.redirect_stdout` só redireciona `sys.stdout` (objeto Python).
PyMuPDF (fitz) escreve diretamente no **fd 1 nativo** via C. Resultado: linhas
como `"Using Tesseract for OCR processing."` aparecem no stdout mesmo com
`redirect_stdout` ativo — corrompe o protocolo JSON da GUI.

### Padrão canônico (`_silenciar_stdout_nativo` em `core/cli.py`)

```python
import os, contextlib

@contextlib.contextmanager
def _silenciar_stdout_nativo():
    """Redireciona fd 1 → fd 2 (stderr) ao redor de chamadas C que escrevem no stdout nativo."""
    stdout_fd = sys.stdout.fileno()
    stdout_copia = os.dup(stdout_fd)
    try:
        os.dup2(sys.stderr.fileno(), stdout_fd)
        yield
    finally:
        os.dup2(stdout_copia, stdout_fd)
        os.close(stdout_copia)
```

---

## 6. Colisão de nomes de saída no batch

### Problema
Desambiguação só por `stem` → `a.pdf` + `a.docx` → dois workers escrevem em
`a.md` concorrentemente → o último vence, o primeiro se perde silenciosamente.

### Padrão canônico (`_nome_destino_unico` em `batch.py` — o 1º arquivo mantém `<stem>.md`)

```python
def _nome_saida(arq: Path, usados: set[str]) -> str:
    """Gera nome de saída único incluindo extensão original para desambiguação."""
    ext = arq.suffix.lstrip(".").lower()
    candidato = f"{arq.stem}-{ext}.md"   # ex: relatorio-pdf.md, relatorio-docx.md
    contador = 2
    while candidato in usados:
        candidato = f"{arq.stem}-{ext}-{contador}.md"
        contador += 1
    return candidato
```

---

## 7. Path traversal — detecção por componente

### Problema
`".." in str(path)` dá falso positivo em `relatorio..final.pdf`.

### Padrão canônico

```python
# ✅ detecta só segmentos ".." reais
if ".." in path.parts:
    raise ValueError(f"Path contém traversal inválido: {path.name}")

# ❌ falso positivo em nomes como "relatorio..final.pdf"
if ".." in str(path):
    raise ValueError(...)
```

---

## 8. Confinamento de path — camada GUI, não core

### Decisão
O confinamento `home-only` (reject paths fora de `~/`) vive na **GUI bridge**
(BatchProcessor.swift), não no `core` Python.

**Motivo:** o core é chamado também pelo CLI, que tem casos legítimos fora do
home (`/tmp`, `/Volumes`, path de CI). Colocar o confinamento no core quebraria
o CLI. Threat model por camada: a GUI expõe a superfície de ataque (drag-drop,
paste), não o CLI.

---

*Última atualização: 2026-05-30 (Ciclos 1–6)*
