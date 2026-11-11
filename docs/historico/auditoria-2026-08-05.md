# Auditoria do projeto pdf2md — 2026-08-05

> Snapshot de agosto de 2026, arquivado em 2026-11-10 — não reflete o estado atual do código.

> Orquestrador: fullstack-agent-system/orchestrator (4 subagentes paralelos:
> segurança, build/deps/CI, código/arquitetura, GUI/processo).
> Verificação adicional: binário PyInstaller, pip-audit, detect-secrets.
> Recomenda-se validação cruzada com **Kimi K3 max** dos itens marcados [KIMI].

---

## 1. CRÍTICO — o `.app` não converte (bug pré-existente desde v0.4.0)

`core/cli.py` expõe `converter` como **subcomando** (`@app.command()`), mas:

- `gui/PDF2MD/BatchProcessor.swift:108` monta args **sem** `"converter"`:
  `pdf2md <arquivo> <destino> --json` → **"No such command"** (verificado
  no binário congelado: exit com erro). Toda conversão da GUI falha.
- README (`Convert single file: pdf2md file.pdf output/`) e todos os
  exemplos usam a forma sem subcomando — forma que não existe.

**Impacto:** desde v0.4.0 o `.app` distribuído não converte (o fluxo de
qualidade, avisos e LLM nunca rodam). O plano-imagens-pdf (Parte 3)
analisou o toggle LLM como "no-op" — o no-op maior era a conversão inteira.

**Fix recomendado:** mover as options de `converter` para o callback do app
(`invoke_without_command=True`) — `pdf2md <file> <out> --imagens ...` vira a
forma canônica; `llm` continua subcomando; testes atualizados; binário
congelado revalidado com os args exatos da GUI.

## 2. CRÍTICO — Pillow 12.2.0 com 16 CVEs (runtime)

`pip-audit`: PYSEC-2026-2253…57, 3451…54, 3493…96 — todas com fix
`12.3.0`. Pillow processa **bytes não confiáveis** de PDF/DOCX
(`image_converter.py`, `doc_converter.py`). CI (`ci.yml` pip-audit) quebrado.
**Fix:** `Pillow>=12.3.0` + `uv lock`.

## 3. ALTA — avisos de qualidade perdidos na GUI

`BatchProcessor.swift:209-212` — `atualizarProgresso` reconstrói
`ProgressoArquivo` sem `avisos` (decodificados na :182). `statusIcon`
(ContentView.swift:332) nunca mostra âmbar. Viola ADR-0005 ("avisos via
lista `avisos` no JSON/CLI/GUI").

## 4. ALTA — `cancelar()` é no-op no app atual

Após o refactor do `ProcessRunner`, `processoAtivo` (BatchProcessor.swift:48)
nunca é atribuído (só zerado) — o cancelamento real depende do
`tarefaConversao.cancel()` → `onCancel` do `withTaskCancellationHandler`,
que usa o box local (funciona), mas o campo `processoAtivo` é código morto
que engana. Unificar ou remover.

## 5. MÉDIA — baseline de segredos vazio quebra o pre-commit/CI

`.secrets.baseline` tem `"results": {}`; detect-secrets acusa 2 falsos
positivos (ADR-0004:69, test_llm_enhancer.py:67 — strings intencionais de
teste/doc). **Fix:** `detect-secrets audit` + update do baseline.

## 6. MÉDIA — sandbox/Keychain sem registro em SECURITY.md

App não-sandboxed (sem entitlements) — `com.apple.security.network.client`
não se aplica (ok p/ Ollama local); Keychain com service fixo sem
`kSecAttrAccessGroup` → qualquer processo do mesmo usuário que conheça o
service lê a key (risco aceito para distribuição ad-hoc, mas **não
documentado em SECURITY.md** — só no ADR-0007).

## 7. MÉDIA — teste-fantasma com rede real

`tests/test_llm_enhancer.py:18` — `from core.llm_enhancer import testar`
faz o pytest **coletar e executar** `testar()` (chamada real ao Ollama,
sem asserção). Fix: `__all__` no módulo ou rename do import.

## 8. MÉDIA — performance da GUI

- `ContentView.swift:26` — `llmConfigurado` chama `KeychainHelper.ler()`
  (IPC securityd) a **cada body eval** (drag-over dispara dezenas/s).
- `SettingsView.swift` — cada tecla no SecureField/URL dispara `salvar()`
  + 2 subprocessos (`llm modelos`/`llm testar`). Precisa debounce ~400ms.

## 9. MÉDIA — dependências/empacotamento

- `python-docx` em runtime, usado **só em testes** → mover p/ dev.
- `THIRD-PARTY-LICENSES.md`: faltam python-pptx (MIT) e openpyxl (MIT)
  embarcados; lista python-docx sem estar.
- CI usa `pip install ".[dev]"` (drift com uv); sem gitleaks no CI; build
  100% local (nada valida o PyInstaller no PR).
- README: `pip3 install "pdf2md[dev]"` falha (não publicado no PyPI).

## 10. MÉDIA — código (staleness pós-T19)

- Docstrings "PDF-only" obsoletas: `converter.py:4`, `batch.py:147-148`,
  `utils.py:48,54` (DOCX também respeita `--imagens`).
- `_MARGEM_PADRAO_PCT` (`converter.py:36`) — código morto.
- `_extrair_chunks_markdown` engole exceção sem aviso (silencioso).
- `pdf_to_md`/`doc_to_md` ainda com 7 params de assets + `_montar_contexto`
  duplicado com resolução **inconsistente** (converter não faz `.resolve()`
  no assets_dir — bug latente de `/var`→`/private/var` em chamada direta).
- Tabela MD duplicada 3× (pptx_converter, xlsx_converter ×2).
- `_MIME_PARA_EXT` duplica `_EXTENSOES_SEGURAS`.
- Cobertura 87%; lacunas: cli.py 67%, branches de limite/bytes de
  `_persistir_render_pagina`, `_base_ollama`/`_visao_modelo`.

## 11. BAIXA — processo/git

- Push direto em main (commit uv.lock) — viola CLAUDE.md (branch
  protection permitiu com bypass reportado). [KIMI]
- PR #18 (layout) precisa rebase (conflita com llm-picker em
  ContentView.swift); PR #19 (xlsx) pronto p/ merge.
- Branches stale: `feat/browse-paste-fixes`; `composer_*.md` solto na raiz;
  `tasks/todo.md` totalmente stale; `KIMI-BRIEFING.md` histórico (ok).
- CHANGELOG [0.6.0] omite a Emenda 2 do ADR-0005 (fixes do review).
- "pdf2md" duplicado na janela (título + header).
- I/O síncrono no main thread (`colarImagem`), `ProcessoBox` Sendable
  informal, `waitUntilExit` sem watchdog. [KIMI]

## 12. OK verificado

- CWE-522 (key só em env) ✓ · CWE-209/532 (erros sanitizados) ✓ ·
  CWE-22 (traversal bloqueado nas fronteiras + containment) ✓ ·
  textutil sem injeção (lista args, binário fixo, timeout) ✓ ·
  thread-safety do batch ✓ (contextos per-call) · `freeze_support`
  desnecessário (ThreadPool) ✓ · uv.lock em sync ✓ · badge CI ✓.

---

## Resumo

**2 críticas** (CLI/GUI não converte; Pillow 16 CVEs) · **7 médias** ·
**6 baixas**. O bug #1 precede todos os PRs desta sessão — os PRs #20/#22
não o introduziram, mas o `.app` v0.6.0 reconstruído continua incapaz de
converter. Validação cruzada recomendada com Kimi K3 max: itens [KIMI].
