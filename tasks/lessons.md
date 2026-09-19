# Lessons Learned — pdf2md

<!-- Máximo 30 entradas. Consolidar similares antes de adicionar. -->
<!-- Formato: [data] Padrão: descrição -->

## Empacotamento & Infra (Bastion)

- `[2026-05-29] CONSTRAINT` **Binário PyInstaller = PATH mínimo.** Todo subprocess de binário de sistema precisa resolver o path explicitamente (fallback Homebrew). Padrão: `_resolver_<bin>()`. Ver `docs/Standards-Anti-Patterns.md §1`.
- `[2026-05-29] PATTERN` **DMG com binário >80MB**: imagem RW de tamanho explícito + detach por device node `-force`. `create -srcfolder` com symlink `/Applications` estoura. Ver `docs/Standards-Anti-Patterns.md §2`.
- `[2026-05-29] PATTERN` **PyInstaller + concorrência**: `ThreadPoolExecutor`, nunca `ProcessPoolExecutor`. `spawn` re-executa o binário congelado com flags que Typer rejeita.
- `[2026-05-29] CONSTRAINT` **Build de release** vive em script versionado (`scripts/build_app.sh`), nunca comandos one-off.
- `[2026-08-13] FAILURE` **`detect-secrets scan --baseline` é gate VÁCUO**: auto-atualiza o baseline e sai 0 mesmo com segredo novo. `--fail-on-unknown` NÃO existe no 1.5.0 (CI quebrou com `unrecognized arguments`). `detect-secrets-hook` retorna 1 sempre (reescreve line_number/generated_at). Gate real: hook + grep `"ERROR: Potential secrets"` no output → exit 1.
- `[2026-08-13] FAILURE` **Smoke de contrato ≠ smoke de valor**: `grep '"ok"'` casa com `"ok": false` (vácuo); `grep '"ok": true'` falha sempre quando o ambiente não tem o serviço (CI sem Ollama). Gate de contrato JSON: `grep -qE '"ok": (true|false)'` — valida shape, não resultado.

## Backend & Core (Stratum)

- `[2026-05-29] FAILURE` **Colisão de stem no batch**: `a.pdf` + `a.docx` → `a.md` colisão sob ThreadPool. Desambiguar por extensão: `{stem}-{ext}.md`. Ver `batch._nome_saida()`.
- `[2026-05-29] FAILURE` **Decode de binário externo**: nunca `text=True` em subprocess de ferramenta legada (antiword emite Latin-1). Capturar bytes + decodificar `utf-8 → cp1252 → latin-1`. Ver `doc_converter._decodificar_bytes()`.
- `[2026-05-29] FAILURE` **fd1 nativo vs sys.stdout**: PyMuPDF escreve no fd C-level. `redirect_stdout` Python não pega. Usar `os.dup2` para proteger protocolo JSON. Ver `converter._silenciar_stdout_nativo()`.
- `[2026-05-29] PATTERN` **PDF em uma passada**: `pymupdf4llm.to_markdown(path, page_chunks=True)` uma vez, não por-página. Re-abrir+reparsear por página é O(n).
- `[2026-05-29] FAILURE` **Display de duração**: `.1f` arredonda conversões em ms para "0.0s". Sub-segundo exibir em ms (`15ms`).

## Swift / GUI (Facet)

- `[2026-05-29] FAILURE` **Deadlock de pipe**: não ler `readDataToEndOfFile()` após `waitUntilExit()` com stderr não drenado. Pipe (~64KB) enche, filho bloqueia em `write()`. Drenar stdout + stderr concorrentemente.
- `[2026-05-29] FAILURE` **Estado de cancelamento com dono dividido**: corrida ao reconverter. Dono único: `cancelar()` só termina processo; loop liquida estado de UI.
- `[2026-05-29] PATTERN` **Confinamento de path por componente**: `path == home || path.hasPrefix(home + "/")`. `hasPrefix(home)` sem barra deixa `/Users/bob` prefixar `/Users/bobby`.
- `[2026-05-30] PATTERN` **alertaColar (bool) → erroColagem (String?)**: binding Bool não re-dispara alert se já `true`. String? com reset no topo da função garante re-trigger em falhas consecutivas.
- `[2026-05-30] PATTERN` **UUID para filename de paste**: `Int(Date.timeIntervalSince1970)` (granularidade de segundo) causa colisão em uso rápido. Usar `UUID().uuidString`.
- `[2026-05-30] CONSTRAINT` **limpar() deve deletar arquivos temp**: paste salva PNGs em `~/Library/Caches/pdf2md/pastes/`. `limpar()` deve chamar `FileManager.removeItem` para cada URL neste diretório antes de `removeAll()`.
- `[2026-08-13] FAILURE` **Nunca filtrar por igualdade de UTI hardcoded**: `UTType(filenameExtension:)` resolve `.docx` → `org.openxmlformats.wordprocessingml.document` (SEM `officedocument.`), mas a lista hardcoded usava a variante COM o segmento → `contains()` falhava e o drop/picker rejeitavam docx/pptx/xlsx silenciosamente. Comparar por EXTENSÃO case-insensitive (espelhando `core/utils.py EXTENSOES_PERMITIDAS`); `UTType` do picker derivados das extensões, não de strings UTI.

## Segurança (Sentinel)

- `[2026-05-29] PATTERN` **Traversal por componente**: `".." in path.parts`, não `".." in str(path)`. String dá falso positivo em `relatorio..final.pdf`.
- `[2026-05-29] CONSTRAINT` **Mensagens de erro** não expõem path absoluto do usuário — usar `.name`.
- `[2026-05-29] DECISION` **Confinamento home-only** vive na GUI bridge, não no core. Core é chamado pelo CLI com paths legítimos fora do home.

## OCR & Dados (Neuron)

- `[2026-05-29] PATTERN` **Memoizar config Tesseract**: `lru_cache` em `_configurar_tesseract_cmd`. Um subprocess `tesseract --version` por arquivo é desperdício.
- `[2026-05-29] FAILURE` **pytesseract + stderr não-UTF8**: imagens em branco geram stderr Latin-1 do Tesseract → `UnicodeDecodeError`. Mesma classe do bug antiword.

## Processo (Maestro)

- `[2026-09-19] FAILURE` **Teste cujo input foi alterado para casar com a implementação não prova nada.** Quando o código diverge da spec, validar a premissa contra o dado real antes de reescrever o algoritmo. Três rodadas: v1 com `"Work\ufffDown"` (input que não existe em PDF), v2 com algoritmo complexo que truncava plural, hedge `or len(md) > 20` que neutralizava a assertion. Mutation testing é o gate final.
- `[2026-05-29] FAILURE` **Gate pulado com auto-avaliação**: Gate 2 do Ciclo 2 declarado "score estimado ≥85" sem evidência → 15 bugs (3 críticos) shippados. Gate exige evidência real.
- `[2026-05-29] DECISION` **Code-review max-effort** (5 finders + verify + sweep) é gate pré-release obrigatório, não faxina posterior. ROI comprovado: 15/15 bugs capturados.
- `[2026-05-29] DECISION` **Nova dep externa via subprocess** → `grep subprocess.run core/` + comparação com resolvers existentes antes de empacotar.
- `[2026-05-29] PATTERN` Distinguir falha ambiental de regressão: validar contra base limpo (`git stash`) antes de culpar a mudança.


## 2026-08-05 — Ciclo de auditoria (Grok 4.5 max)
- **Push direto em main é bloqueado pela branch protection** (CLAUDE.md PR-only) — o commit `4572299 chore: uv.lock` passou por bypass reportado; registrar e nunca repetir.
- **Typer 0.26 usa click vendored** — `invoke_without_command` não funciona como no click clássico. Default-command deve ser resolvido no entry point (shim `core.cli:main`).
- **PyInstaller executa o módulo como `__main__`** — guards `if __name__` precisam ficar no FIM do módulo.
- **`.secrets.baseline` vazio quebra o CI detect-secrets** — falsos positivos intencionais devem ser auditados e baselined.
- **Bug CLI/GUI durou 2 releases (v0.4.0→v0.6.0)**: o `.app` nunca converteu — o smoke de empacotamento com os args EXATOS da GUI agora roda no CI.
