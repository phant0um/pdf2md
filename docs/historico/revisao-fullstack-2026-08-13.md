# Revisão Fullstack Agent System — pdf2md v0.6.0 (2026-08-13)

> Snapshot de agosto de 2026, arquivado em 2026-11-10 — não reflete o estado atual do código.

> Revisão orquestrada pelo Maestro (fullstack-agent-system) com 5 especialistas em paralelo:
> stratum (backend), facet (frontend), sentinel (security), bastion (infra/CI), probe (QA).
> Escopo: working tree no branch main (3 Swift modificados sem commit + docs de auditoria untracked).

## Veredito geral

**APROVADO COM RESSALVAS** — 0 findings bloqueantes. 2 ALTAS, ~15 MÉDIAS, ~12 BAIXAS.

| Agente | Domínio | Veredito | Destaque |
|---|---|---|---|
| stratum | Backend | APROVADO COM RESSALVAS | Ruff limpo; 2 ALTAS (crash unicode, memory-bomb DOCX) |
| facet | Frontend | APROVADO COM RESSALVAS | Cancelamento robusto OK; erros de subprocess invisíveis |
| sentinel | Security | PASS COM RESSALVAS | pip-audit 0 CVEs, gitleaks limpo, sem blockers |
| bastion | Infra/CI | APROVADO COM RESSALVAS | Pipeline reprodutível; 2 gates de segurança vácuos |
| probe | QA | 206 passed / 0 failed | Cobertura 87%; cli.py 68% é o buraco |

## Evidência coletada

- `pytest tests/ -v` → **206 passed, 0 failed, 0 skipped** (28,70s, 8 warnings)
- `pytest --cov=core` → **TOTAL 1123 stmts / 142 miss / 87%**
- `pip-audit` (árvore completa do lock) → "No known vulnerabilities found"
- `gitleaks git` → 51 commits, no leaks found
- `detect-secrets scan --baseline` → sem novos segredos
- `ruff check core/ tests/` → All checks passed
- `mypy strict core/` → exit 1, 21 erros (4 reais, 17 stubs ausentes)

## Findings

### ALTAS

| # | Arquivo:linha | Problema | Fix |
|---|---|---|---|
| 1 | image_assets.py:111 + batch.py:186 | `prefixo = f"{destino.stem}__"` com stem contendo espaço/acento ("relatório.pdf") gera nome fora do regex ASCII `^[A-Za-z0-9._-]+$` → ValueError → arquivo inteiro vira ERRO em `--obsidian`/`--assets-dir` (mesmo caminho em doc_converter `_montar_handler`) | Sanear prefixo (slugify ASCII) em `_resolver_assets` |
| 2 | doc_converter.py:211-213 | `f.read()` lê a imagem inteira em memória antes do check de 50MB em `registrar_asset` — docx malicioso com imagem de 2GB derruba o processo | Checar tamanho via seek/tell antes de ler (ou ler em chunks até o limite) |

### MÉDIAS — backend (stratum)

| # | Arquivo:linha | Problema | Fix |
|---|---|---|---|
| 3 | converter.py:134-135 | Falha do `pymupdf4llm.to_markdown` engolida → fallback silencioso para texto bruto sem aviso | Anexar aviso em `contexto.avisos` no fallback |
| 4 | converter.py:234-238 | Aviso de limite anexado a cada página de scan (10k págs → ~9,5k avisos duplicados); `doc_converter.py:203-208` já tem guard `limite_avisado` | Mesmo padrão de dedup de aviso |
| 5 | cli.py:58 | Default-command quebra com flags antes do positional: `pdf2md --sobrescrever in.pdf out/` → "No such option" | Aceitar flags no prefixo (parse parcial) ou documentar |
| 6 | xlsx_converter.py:66 | `list(ws.iter_rows(...))` derrota o streaming `read_only=True` → planilha de 1M linhas materializada em memória | Iterar em 2 passadas (header + corpo) ou chunked |
| 7 | xlsx_converter.py:71-74 | Sheet sem header (linhas iniciais vazias) → `not any(headers)` → conteúdo descartado silenciosamente | Usar primeira linha não-vazia como header ou emitir aviso |
| 8 | image_converter.py:118-119 | `TesseractError` (lang pack `por` ausente) vira "arquivo corrompido" — mensagem enganosa | Capturar `pytesseract.TesseractError` separadamente |
| 9 | llm_enhancer.py:409 | `read_bytes()` + base64 da imagem inteira sem limite (render 300dpi ≈ 35MB base64) | Reusar `_MAX_BYTES_IMAGEM` e avisar |
| 10 | cli.py:200-201 | Com `--json`, markup rich `[red]...[/red]` é emitido dentro do campo JSON `erro` — GUI renderiza cru | Mensagem limpa no caminho `--json` |

### MÉDIAS — frontend (facet)

| # | Arquivo:linha | Problema | Fix |
|---|---|---|---|
| 11 | ProcessRunner.swift:87-91 + BatchProcessor.swift:159-161 | stderr drenado e descartado; `terminationStatus` nunca verificado → todo erro vira "Falha ao executar processo" genérico | Retornar (stdout, stderr, exitCode) e exibir stderr quando exit != 0 |
| 12 | BatchProcessor.swift:58-61 | Binário ausente do bundle: só `print` no console; UI não mostra nada | `@Published` de erro fatal + alerta visível |
| 13 | BatchProcessor.swift:112-119 | Sanitização home só para a origem; destino/vault não validados no Swift (CLI rejeita → erro genérico) | Mesma checagem na GUI ou propagar stderr |
| 14 | SettingsView.swift:106-114 | Keychain salvo (delete+add) a cada keystroke do SecureField; retorno Bool ignorado — falha de persistência silenciosa | Salvar em commit/blur e alertar em falha |

### MÉDIAS — infra/CI (bastion)

| # | Arquivo:linha | Problema | Fix |
|---|---|---|---|
| 15 | ci.yml:106 | Smoke `llm testar` usa `grep -q '"ok"'` que casa com `"ok": false` (CLI falha com exit 0) → gate vácuo | `grep -q '"ok": true'` |
| 16 | ci.yml:45 | `detect-secrets scan --baseline` sem `--fail-on-unknown` sai 0 mesmo com segredo novo | Adicionar `--fail-on-unknown` |
| 17 | ci.yml:1-7 | Sem bloco `permissions:`; GITHUB_TOKEN com escopo padrão (read/write) | `permissions: contents: read` |
| 18 | ci.yml:14,17,22,61,65,69 | Actions pinadas por tag flutuante (`@v4`), não por SHA — risco supply-chain | Pinar por SHA (Dependabot gerencia) |
| 19 | pyproject.toml:69-73 + ci.yml:30-34 | `[tool.mypy] strict` e mypy no extra dev, mas CI não roda mypy (config morta + 21 erros reais no local) | `uv run mypy core/` no job test |
| 20 | SECURITY.md:5-7 | Tabela "Supported Versions" lista só 0.1.x; versão atual é 0.6.0 | Atualizar |
| 21 | ci.yml:4-7 | `push: branches: ["**"]` + `pull_request`: PR do mesmo repo roda pipeline 2×; WIP paga test+build completos | `paths-ignore`, `concurrency: cancel-in-progress` ou build só em main/tags |

### BAIXAS (não bloqueiam)

- **batch.py:292** — docstring "varre recursivamente (não recursivo por padrão)" contraditória
- **cli.py:45** — `--version`/`--completion` em `_COMANDOS_TOPO` não existem no app Typer
- **cli.py:133** — `workers` sem clamp (0 ou 10000 → ValueError/threads demais)
- **quality.py:128** — docstring diz BOM preservado, mas `replace("\ufeff","")` remove
- **image_assets.py:53-54** — docstring diz `total` conta duplicatas; código não incrementa (comportamento correto p/ dedup)
- **converter.py:106** — PDF aberto 2x (fitz.open + to_markdown reparseia por path)
- **llm_enhancer.py:346** — N+1: GET /api/show por modelo
- **batch.py:399** — `as_completed` → ordem não determinística na tabela do CLI
- **image_converter.py:14** — `register_heif_opener()` side-effect no import
- **doc_converter.py:43** — `_TEXTUTIL_PATH` hardcoded (justificável: binário fixo do macOS)
- **ProcessRunner.swift:38** — `onProcesso` morto após refatoração de cancelamento
- **ContentView.swift:251-254** — drop aceita qualquer UTType.fileURL (`.app`, `.dmg` entram)
- **ContentView.swift:367-368** — reconversão de existente → status "ignorado" sem explicação
- **ci.yml:24-25,74-75** — Tesseract instalado 2× sem cache Homebrew
- **ci.yml:51** — gitleaks via brew (versão flutua) vs pre-commit pinado v8.18.4
- **ci.yml:11,57** — `macos-latest` flutua; sem matriz
- **ci.yml:31** — roda `ruff check` mas não `ruff format --check`
- **docs/adr/** — numeração pula 0005→0007 (ADR-0006 inexistente)
- **.gitleaks.toml:17-20** — 3 regexes idênticas duplicadas no allowlist
- **dist/** — `rw.22919.PDF2MD-v0.1.0.dmg` órfão (115MB, hdiutil interrompido)

### Segurança — verificações limpas (sentinel)

- **Command injection:** único subprocess é `textutil` com path fixo + lista de args + timeout (CWE-78 mitigada)
- **Path traversal:** nomes de asset 100% gerados + regex ASCII + containment (CWE-22 OK)
- **Segredos:** key LLM só Keychain → env, nunca argv (CWE-522 OK)
- **Exposição:** erros sanitizados em todas as fronteiras (CWE-209/532 OK)
- **Dependências:** 0 CVEs conhecidas

### Findings de segurança não-bloqueantes (backlog)

| Sev | CWE | Achado |
|---|---|---|
| MEDIA ~5.3 | CWE-94/77 | Prompt injection: texto do documento vai ao LLM sem instrução de isolamento (llm_enhancer.py:137-154); saída gravada sem validação (batch.py:122) |
| BAIXA-MEDIA ~3.7 | CWE-918 | SSRF: URL LLM sem blocklist de IP privado/metadata — aceito (ADR-0004/0007, URL vem do usuário) |
| BAIXA ~3.0 | CWE-367 | TOCTOU em symlink check (image_assets.py:93-98) — fora do threat model desktop |
| BAIXA ~2.6 | CWE-400 | Render 300dpi sem teto de páginas/dimensão (converter.py:208-211) — OOM antes dos limites |

## Gaps de teste (probe)

| Sev | Módulo (cov%) | Gap |
|---|---|---|
| ALTA | cli.py (68%) | Contrato JSON/exit-codes do CLI mal testado — regressão no bridge Swift passaria despercebida; `llm` sem `--json`, Tesseract ausente, `--vault` sem teste |
| ALTA | image_converter.py (75%) | `_normalizar_png`/GIF/PPM nunca executam; imagem corrompida; fallback OCR-curto→LLM sem teste |
| MEDIA | pdf_images.py (79%) | Imagem danificada, dados vazios, CMYK→RGB, falha `_converter_png` sem teste |
| MEDIA | batch.py (93%) | Exceção real no worker, colisão de stem 3+, subdiretório sem teste; **cancelamento de batch é gap de feature** |
| MEDIA | llm_enhancer.py (94%) | TimeoutError, content não-str, resposta não-dict, URL inválida, JSON malformado sem teste |
| MEDIA | doc_converter.py (87%) | Error paths de extração de assets (modo ambos/extrair com imagem corrompida) |
| BAIXA | __main__.py (0%) | Entry point `python -m core` nunca exercitado |

⚠️ **Teste-fantasma:** pytest coleta `testar` importado de `core.llm_enhancer` como teste (PytestReturnNotNoneWarning) — faz chamada de rede real a localhost:11434 durante a suíte. Aberto desde a auditoria de 05/08 (M5).

## Estado dos achados da auditoria anterior (05/08, Grok)

| Achado | Estado |
|---|---|
| C1 CLI/GUI mismatch | CORRIGIDO (v0.6.0) |
| C2 Pillow 13 CVEs | CORRIGIDO (12.3.0, pip-audit limpo) |
| A1 avisos descartados | CORRIGIDO |
| B7 waitUntilExit sem watchdog | CORRIGIDO no working tree (SIGTERM→fecha pipes→SIGKILL + timeout 30min) — **diff não commitado** |
| M5 teste-fantasma `testar` | ABERTO |
| M6 Keychain por keystroke | ABERTO |

## Plano de ação

- **Fase 1 (ALTAS):** findings 1-2 — stratum, TDD
- **Fase 2 (contrato/UX):** findings 3-14 — stratum (backend) + facet (frontend)
- **Fase 3 (gates CI):** findings 15-21 — bastion
- **Fase 4 (testes):** gaps cli.py/image_converter + teste-fantasma — probe
- **Pós-fix:** pytest + ruff + mypy verdes; forge (5E) sobre o diff; PR para main
