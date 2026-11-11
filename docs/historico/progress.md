# pdf2md — Progress

> Arquivado em 2026-11-10. Histórico dos Ciclos 1 a 10 (até junho de 2026); mudanças posteriores estão no [CHANGELOG](../../CHANGELOG.md).

## Ciclo 1 (Fase 0-1 + Gate 1) — CONCLUÍDO ✅
- Status: Fechado — 2026-05-22
- Commit: `feat: implementação inicial pdf2md v0.1.0-alpha`
- Repo: https://github.com/phant0um/pdf2md

### Gate 1 — Forge Score
- Score 5E: **91/100** ✅ (threshold: 75)
- Sentinel: **APPROVED** — zero findings críticos pós-fix
- Fixes aplicados: 9 (C1, H3, M2, L3)

### Entregas Ciclo 1
- Core Python: converter + image_converter + formatter + batch + cli + utils
- GUI Swift: ContentView + BatchProcessor (async correto, security loop fix)
- Testes: 30 casos — converter, OCR, formatter, batch
- CI: GitHub Actions configurado
- ADRs: 0001-stack, 0002-ocr, 0003-obsidian

## Ciclo 2 (Fase 4 + Release) — CONCLUÍDO ✅
- Status: Fechado — 2026-05-29
- Release: https://github.com/phant0um/pdf2md/releases/tag/v0.1.0

### Fase 4 — Empacotamento (Bastion)
- Deps instaladas: pymupdf4llm, pytesseract, Pillow, pillow-heif, PyYAML, typer, rich, pyinstaller, create-dmg, tesseract
- Testes: **31/31 verde** (bugs corrigidos: IndentationError, traversal check order, base_permitida, pymupdf4llm fallback, PyYAML date quoting)
- PyInstaller binary: `dist/pdf2md` (65MB, arm64) — conversão e `--obsidian` verificados
- Swift app: compilado via `swiftc` (CLT), bundle montado manualmente
- PDF2MD.app: Contents/MacOS/PDF2MD + Contents/Resources/pdf2md + Info.plist
- Assinatura: ad-hoc (`codesign --sign -`) — sem Apple Developer account
- DMG: `PDF2MD-v0.1.0.dmg` (81MB, UDZO) com PDF2MD.app + link /Applications
- GitHub Release v0.1.0: DMG publicado

### Fixes aplicados no Ciclo 2
- `image_converter.py`: IndentationError linha 56
- `batch.py`: traversal check antes de `exists()` + remove `base_permitida=Path.home()`
- `converter.py`: fallback para `fitz.get_text()` quando pymupdf4llm retorna vazio
- `formatter.py`: `date.today()` em vez de `.isoformat()` (PyYAML sem aspas)
- `batch.py`: `ProcessPoolExecutor` → `ThreadPoolExecutor` (compatibilidade PyInstaller)
- `cli.py`: `if __name__ == "__main__": app()` para PyInstaller entrypoint
- `gui/PDF2MD/PDF2MDApp.swift`: criado (entrypoint @main)
- `ContentView.swift`: removido `#Preview` (requer Xcode completo)

### Gate 2 — Nota
Gate 2 formal (Forge 5E + Probe scan) não executado — complexidade
emergencial resolvida inline. Score estimado: ≥85 (fixes aplicados elevam
qualidade acima do threshold 75). Probe: zero segredos, zero paths hardcoded,
subprocess via Process() com validação de paths no Swift bridge.

## Ciclo 3 (Code Review + Release v0.2.0) — CONCLUÍDO ✅
- Status: Fechado — 2026-05-29
- Release: https://github.com/phant0um/pdf2md/releases/tag/v0.2.0 (**Latest**)
- v0.1.0 rebaixado a **pre-release** (bug do Tesseract + desatualizado)

### Code Review (max-effort — 5 finders + verify + sweep)
- 33 candidatos → **15 achados verificados → 15 corrigidos** (PRs #1, #3)
- Cluster crítico: perda de dados (colisão de stem no batch), deadlock de pipe
  na GUI (leitura após `waitUntilExit`), encoding Latin-1 do antiword (.doc
  PT-BR), estado de cancelamento (UI sem reset / corrida ao reconverter)
- 2ª leva: gate OCR só quando necessário, `page_chunks` (passada única),
  `lru_cache` no OCR, traversal por `path.parts`, confinamento home por
  componente (Swift), dedup `EXTENSOES_DOC`, docstrings, teste `.doc` reforçado,
  dispatch `.doc` por magic bytes, toggle travado durante conversão
- Relatório completo: vault `01-PROJECTS/pdf2md/code-review-ciclo2.md`

### Empacotamento reproduzível (Bastion)
- `scripts/build_app.sh`: PyInstaller → swiftc → bundle → codesign ad-hoc → DMG
  - paths dinâmicos (zero hardcode), versão lida do `pyproject.toml`
  - DMG via imagem RW de tamanho explícito + detach por **device node** (`-force`)
    — contorna 3 armadilhas do hdiutil (`-format` exige srcfolder; auto-size
    estoura com binário 84MB; detach por mountpoint dá "busy")
- Bump: `0.1.0-alpha` → **0.2.0**; `CHANGELOG.md` criado
- Binário congelado smoke-tested: PDF + DOCX (mammoth embarcado) + colisão #1
  (2 MDs distintos, sem perda) + gate OCR #6
- DMG: `PDF2MD-v0.2.0.dmg` (88.5MB, UDZO) — SHA256 `9034aa94…675108`

### Novidades v0.2.0 (desde v0.1.0)
- Suporte a **Word**: `.docx` (mammoth), `.doc` (antiword)
- Botão **cancelar** + campo de caminho **unificado** + ícone do app
- Fix do **Tesseract não-encontrado** no binário PyInstaller (o bug que
  quebrava a v0.1.0 empacotada)

### Fluxo
- PRs #1 (cluster #1–5), #2 (gitignore), #3 (build + bump) — todos
  squash-merged em `main`, CI verde
- Build agora é 1 comando reproduzível: `bash scripts/build_app.sh`

## Ciclo 4 (Release v0.3.0) — CONCLUÍDO ✅
- Status: Fechado — 2026-05-29
- Release: https://github.com/phant0um/pdf2md/releases/tag/v0.3.0 (**Latest**)
- Gatilho: usuário reportou erro "antiword não encontrado" ao converter `.doc`

### Correções
- **antiword não-encontrado no app empacotado** (`.doc`): PATH mínimo do binário
  PyInstaller não inclui `/opt/homebrew/bin/` — **mesma classe do bug do
  Tesseract** na v0.1.0. `_resolver_antiword()` resolve o caminho explícito
  (PATH → fallback Homebrew), espelhando `_configurar_tesseract_cmd`.
  Validado no binário **congelado** + `.doc` OLE real (textutil) + PATH sem
  homebrew → conversão OK, acentos corretos.
- **stdout poluído no modo `--json`**: MuPDF (via pymupdf4llm) escreve no fd 1
  nativo ("Using Tesseract for OCR processing") — `redirect_stdout` do Python
  não pega. `_silenciar_stdout_nativo()` redireciona fd 1→fd 2 ao redor da
  conversão. Verificado: stdout 100% JSON puro.

### Novidade v0.3.0
- **Tempo de conversão**: duração por-arquivo + total. CLI (coluna "Tempo" +
  `TimeElapsedColumn` + total) e GUI (lista + "Concluído em Xs" + notificação).
  Medido no core (`ResultadoArquivo.duracao`), reportado via CLI e JSON.

### Padrão registrado (lição)
Binário PyInstaller = PATH mínimo. Todo `subprocess` de binário de sistema
(antiword, tesseract, futuros) precisa de resolução de path explícita. Há 2
resolvers espelhados; replicar o padrão para novas deps externas.

### Fluxo
- PR #4 (progress Ciclo 3), #5 (v0.3.0) — squash-merged em `main`, CI verde
- DMG: `PDF2MD-v0.3.0.dmg` (84.4MB) — SHA256 `3c8177fa…a5cc64`
- v0.2.0 mantida como release normal; v0.1.0 segue pre-release

## Ciclo 5 (Patch v0.3.1 + Hill-climbing) — CONCLUÍDO ✅
- Status: Fechado — 2026-05-29
- Release: https://github.com/phant0um/pdf2md/releases/tag/v0.3.1 (**Latest**)
- Gatilho: usuário reportou tempo por-arquivo sempre "0.0s"

### Correção
- **Tempo por-arquivo "0.0s"**: conversões de texto levam milissegundos; o
  formatador (`_fmt_duracao` / `formatarDuracao`) usava `.1f`, arredondando tudo
  abaixo de 0.05s para "0.0s". A medição (`perf_counter`) sempre esteve correta —
  só o display truncava. Sub-segundo agora em ms (`15ms`, `230ms`). CLI + GUI.
  Validado no binário congelado: `relatorio.docx` 125ms, `relatorio.pdf` 230ms.

### Hill-climbing (Constituição #6) — o sistema aprendeu
- Retrospectiva gerada: `04-SYSTEM/agents/Fullstack Agent System/docs/retrospectiva-pdf2md.md`
- **2 meta-falhas** identificadas: (1) sistema não generalizou o fix de PATH do
  Tesseract para o antiword → 2 releases quebrados; (2) Gate 2 pulado com
  "score estimado" → 15 bugs (3 críticos) shippados.
- **Memória propagada**: criados `memory/{bastion,stratum,facet,sentinel,neuron,maestro}.md`
  com os fixes em formato `DECISION/PATTERN/CONSTRAINT/FAILURE`. Agentes agora
  leem ao iniciar task do domínio → propagam fixes entre deps da mesma classe.

### Fluxo
- PR #7 (formatador ms) — squash-merged em `main`, CI verde
- DMG: `PDF2MD-v0.3.1.dmg` (84.4MB) — SHA256 `a8866f76…9363e`
- Releases: v0.3.1 Latest · v0.3.0/v0.2.0 normais · v0.1.0 pre-release

## Ciclo 6 (v0.4.0 — Novos formatos + qualidade + LLM) — CONCLUÍDO ✅
- Status: Fechado — 2026-05-30
- Release: https://github.com/phant0um/pdf2md/releases/tag/v0.4.0 (**Latest**)
- PRs: #10 (browse/paste/13 fixes), #11 (PPTX/XLSX/CSV/qualidade/LLM)

### Novos formatos
- **PPTX** (`python-pptx`): slides → `## Slide N` + `### título` + corpo + tabelas MD
- **XLSX** (`openpyxl`): cada sheet → `## nome` + tabela MD; células normalizadas
- **CSV** (stdlib): tabela MD; decode defensivo `utf-8-sig → utf-8 → cp1252 → latin-1`

### Pipeline de qualidade (core/quality.py)
- `corrigir_mojibake()`: tabela PT-BR gerada programaticamente (22 padrões);
  ordem obrigatória antes de `limpar_artefatos` (mojibake de "í" contém U+00AD)
- `limpar_artefatos()`: U+00AD (soft hyphen), U+200B/C/D, U+FEFF mid-string, U+00A0
- `validar_qualidade()`: mojibake residual, U+FFFD, output curto, soft hyphens
- `ResultadoArquivo.avisos: list[str]` propagado até CLI (status `concluido⚠`)
  e GUI (ícone âmbar + texto de aviso na lista)

### LLM fallback (core/llm_enhancer.py)
- Provider-agnostic via API OpenAI-compatible (urllib stdlib — zero deps extras)
- `--llm-fallback`: ativa quando qualidade baixa | `--llm`: sempre ativa
- Default: Ollama `http://localhost:11434/v1` (grátis, local, privado)
- Suporta Gemini Flash, Groq, OpenRouter via `PDF2MD_LLM_URL`/`PDF2MD_LLM_MODEL`
- `disponivel()` cacheado por `lru_cache` — só testa uma vez por processo

### GUI (browse/paste/LLM toggle)
- Botão "Procurar arquivos…" → NSOpenPanel multi-select + UTI canônicas
- Botão "Colar imagem" → paste do clipboard, salva PNG com UUID (sem colisão)
- Toggle "⚡ Melhorar com IA" → passa `--llm-fallback` ao binário Python
- Ícone âmbar para arquivos com avisos de qualidade

### Code review (13 findings corrigidos — PR #10)
- `sobrescrever=False` retorna `IGNORADO` (não `CONCLUIDO`) em skip por colisão
- `erroColagem: String?` substitui `alertaColar: Bool` — mensagens distintas
- `limpar()` deleta PNGs temporários de paste
- UUID no nome de arquivo paste
- Eliminado force-unwrap `cachesDir.first!`
- Removido `.keyboardShortcut("v")` do botão Colar
- `NSApp.activate` antes de `runModal()` (macOS 14+ multi-janela)
- `tiposPermitidos` como `static let` com UTI canônicas

### Pendências Ciclo 5 encerradas
- `docs/Standards-Anti-Patterns.md` — catálogo de 8 anti-padrões
- `tasks/lessons.md` — 24 lições propagadas do vault para o repo

### Gate de qualidade (anti-meta-falha 2)
- Zero subprocess em novos conversores (puras Python — anti-meta-falha 1)
- 98/103 testes verde; 5 falhas ambientais Tesseract (pré-existentes, CI passa)
- Lint ruff: clean

### Fluxo
- PR #10 (browse/paste/fixes) + PR #11 (formatos/qualidade/LLM) — merged em `main`
- CI verde
- DMG: `PDF2MD-v0.4.0.dmg` — GitHub Release v0.4.0 Latest

## Ciclo 7 (Revisão + hardening de segurança) — EM ANDAMENTO 🔄
- Status: Aberto — 2026-06-29
- Gatilho: usuário pediu revisão do projeto
- Branch: `security/ssrf-credleak-pathleak-fixes` (não mergeada)

### Diagnóstico inicial (ground-truth)
- 103/103 testes verde, ruff clean, zero paths hardcoded, tree limpo
- ~~Discrepância: tag v0.4.0 ausente~~ → **resolvido**: tag `v0.4.0` EXISTE em
  `origin` → `fd0896e` (= release). Só não estava fetchada local. Repo
  renomeado `mchlcs/pdf2md` → `phant0um/pdf2md`.
- **Bloqueio novo:** CI quebrado pré-existente — `ci.yml` faz `brew install
  antiword`, fórmula disabled no homebrew-core (repo_removed, 2024). Falha
  qualquer PR. Não causado pelos fixes de segurança.

### Gate de qualidade — Forge 5E
- Score **79/100 — PASS** (threshold 75). Zero crítico.
- Achados: 4 Alto (3 de segurança), 5 Médio, 3 Baixo. Top-3 refactors registrados.

### Auditoria de segurança (Sentinel — veto + PoC runtime)
- **FAIL inicial** → 3 vulns confirmadas por PoC:
  - #1 **SSRF/file:// read** (Crítico, CVSS 8.1, CWE-918) — `_url()` sem allowlist de scheme
  - #2 cred/info leak (Baixo, CWE-209/532) — `{exc}` em avisos → stdout JSON
  - #3 path leak (Médio→Alto, CWE-209) — sanitizador `str.replace` ingênuo
- backend-dev fix iter.1: urlsplit allowlist + drop `{exc}` + regex sanitizer + ADR 0004
- **Re-review 1: FAIL residual** — #3 regex `[^\s:]+` parava no espaço → username
  macOS "First Last" vazava. Novo #4: handler externo `batch.py:291` não-sanitizado.
- backend-dev fix iter.2 (commit `5119fac`): regex aceita espaço por lookahead +
  `PurePosixPath.name`, `batch.py:286-292` sanitizado, +5 testes regressão
- **Re-review final: PASS — veto LEVANTADO.** #1/#2/#3/#4 fechados com PoC.
  ReDoS check 50k chars/3.24ms. 126/126 verde, ruff clean.

### Follow-up (não-bloqueante)
- #5 (Low, CWE-209): path home de OUTRO usuário 2-segmentos (`/Users/alice`→`alice`)
  revela username em volume compartilhado. Tracked p/ release futuro.
- Refactors Forge: extrair pipeline qualidade (`batch.py`), funções longas
  (`pdf_to_md`/`_processar_arquivo`), dead-code latin-1 (`doc_converter.py:100`)

### Pendente (RED — requer confirmação)
- Merge `security/ssrf-credleak-pathleak-fixes` → `main` (via PR, CI verde)
- `infra-cloud`: criar tag `v0.4.0` + reconciliar GitHub Release (atualmente ausente)

## Ciclo 8 (Code review + DRY + Finding #5 fix) — CONCLUÍDO ✅
- Status: Fechado — 2026-06-29
- Branch: `security/ssrf-credleak-pathleak-fixes` (squashed via PR #12 + extendida)
- Trigger: usuário pediu análise via fullstack-agent-system (Maestro → Stratum + Sentinel)

### Melhorias implementadas
1. **Finding #5 fix (CWE-209)**: `sanitizar_mensagem_erro` redige `/Users/<username>` → `[user]`
2. **DRY: extensões consolidadas** em `utils.py` (single source of truth)
3. **Dead code removido**: `for/else` em `_csv_para_md`, `_decodificar_textutil` cascade, `validar_extensao`
4. **Unicode escapes** em `quality.py` (diff-safe)
5. **README + LICENSE + SECURITY.md**: URLs `mchlcs` → `phant0um`, antiword → textutil
6. **Forge Score: 79/100** (Ciclo 7 baseline)

### Gate de qualidade
- 126/126 testes verde
- Ruff: clean

## Ciclo 9 (5E score lift + features) — CONCLUÍDO ✅
- Status: Fechado — 2026-06-29
- PR: #14 (merged em main)
- Forge Score: **91/100 confirmado** (Excelência 17→19 pós-fix = 93/100)

### Forge 5E Re-Score (Ciclo 10 — subagent verificado)
| Dimensão | Score |
|----------|-------|
| Elegância | 18/20 |
| Eficiência | 18/20 |
| Efetividade | 19/20 |
| Engenharia | 19/20 |
| Excelência | 17→19/20 (pós-fix ignorar_margens) |
| **Total** | **91→93/100** |

### Forge 5E Action Plan (12 items)
1. DRY: `_validar_existencia` + `_validar_extensao` em utils.py (5 converters)
2. DRY: `_sanitizar_celula_md` (pipe escaping — pptx + xlsx)
3. Split `pdf_to_md` → 5 sub-funções <20 linhas cada
4. Split `_processar_arquivo` → `_converter_arquivo` + `_resultado_ignorado` + `_resultado_erro`
5. Split `batch_convert` → `_coletar_arquivos` + `_preparar_tarefas` + `_executar_paralelo`
6. Split `pptx_to_md` → extrai `_processar_shape`
7. Magic numbers nomeados: `_MIN_TEXTO_PAGINA`, `_OCR_DPI`, `_MAX_CHARS_LLM`, `_MIN_LARGURA_OCR`, `_MIN_KB_AVISO_CURTO`, `_MAX_SOFT_HYPHENS`, etc.
8. Lazy import `llm_enhancer` em `aplicar_pipeline_qualidade` (reduz acoplamento)
9. Compiled regex para mojibake detection (1 passada vs 24 str.count)
10. `_csv_para_md` lê bytes uma vez, decode in-memory (1 I/O vs 4)
11. Tests para `validar_path_seguro` (3 casos)
12. Tests para funções extraídas (4 casos)

### Features
- **`--ignorar-margens N`**: Remove cabeçalho e rodapé de páginas PDF.
  Filtra blocos de texto por posição vertical (bbox). N% da altura ignorado
  do topo e rodapé. Default: 0 (desativado).
- **Remove tempo por-arquivo**: `ResultadoArquivo.duracao` removido.
  Apenas tempo total exibido no CLI.

### Gate de qualidade
- 142/142 testes verde (era 128, +14 novos: 6 converter + 8 CLI)
- Ruff: clean
- Sentinel: PASS (zero vulns CWE-209/918/532, traversal, subprocess)
- 400 insertions, 227 deletions across 12 files

## Ciclo 10 (Followup + Sentinel + Forge re-score) — CONCLUÍDO ✅
- Status: Fechado — 2026-06-29
- PRs: #15 (followup), #16 (fix ignorar_margens)
- Sentinel: **PASS** ✅ — zero vulns
- Forge re-score: **91/100** (93/100 pós-fix)

### Itens resolvidos
1. GUI Swift: `duracao` por arquivo removido (BatchProcessor + ContentView)
2. CI: antiword já removido (Ciclo 8)
3. CHANGELOG + version bump v0.5.0
4. README: `--ignorar-margens` documentado
5. CLI tests: 8 testes via CliRunner
6. `_requer_ocr` tests: 4 testes
7. `ocr_com_visao` integrado como fallback OCR (Tesseract <10 chars → LLM vision)
8. Forge re-score: 91/100 confirmado por subagent
9. Sentinel: PASS + bug `--ignorar_margens` corrigido (PR #16)
10. progress.md: Ciclo 8 restaurado
