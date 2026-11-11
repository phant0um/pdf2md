# Auditoria principal — pdf2md v0.6.0 (Grok 4.5 max)

> Snapshot de agosto de 2026, arquivado em 2026-11-10 — não reflete o estado atual do código.

> Revisão da auditoria preliminar (`docs/historico/auditoria-2026-08-05.md`) + auditoria independente.
> Evidência obtida por leitura de código e execução local em 2026-08-05.

---

## Concordâncias / discordâncias com a preliminar

| # prelim. | Veredito | Nota |
|-----------|----------|------|
| 1 CLI/GUI | **CONFIRMADO crítico** | Reproduzido: `uv run pdf2md /tmp/arquivo_teste.pdf /tmp/saida` → `No such command '/tmp/arquivo_teste.pdf'`. Com `converter` → OK (730ms). Sem `invoke_without_command`. GUI `BatchProcessor.swift:116` monta `[url.path, dest?, flags, --json]` **sem** `"converter"`. Settings/LLMProbe usa `["llm","modelos|testar","--json"]` — OK. |
| 2 Pillow | **CONFIRMADO crítico** (ajuste contagem) | Pillow **12.2.0** no lock; `pip-audit` falha. **13** PYSEC únicos (2253–57, 3451–54, 3493–96), todos `fix_versions: 12.3.0`. Preliminar disse 16 — duplicatas de advisory. CI `pip-audit` quebra. |
| 3 avisos GUI | **CONFIRMADO alta** | Decode em `:181` tem `item.avisos`; `:182` chama `atualizarProgresso(..., erro:)` sem avisos; `:209-211` reconstrói sem `avisos` → sempre `[]`. `statusIcon` âmbar morto. |
| 4 cancelar | **PARCIAL — rebaixa para média** | `processoAtivo` nunca atribuído (`:48`, só `= nil` em `:188`). `cancelar()` (`:203-207`) é no-op. **Porém** `ContentView.cancelarConversao` faz `tarefaConversao?.cancel()` → `onCancel` do `withTaskCancellationHandler` termina via `ProcessoBox` (`:160-162`). Cancel **funciona**; código morto/enganoso. |
| 5 baseline | **CONFIRMADO média** | `.secrets.baseline` → `"results": {}`. |
| 6 Keychain/sandbox | **CONFIRMADO média** | Sem entitlements; Keychain service fixo `com.pdf2md.llm` sem access group (`KeychainHelper.swift:14-15`). Documentado em ADR-0007, **ausente** em `SECURITY.md`. |
| 7 teste-fantasma | **CONFIRMADO média** | `pytest --collect-only` lista `tests/test_llm_enhancer.py::testar` (import de `llm_enhancer.testar`). Rede real sem assert. |
| 8 perf GUI | **CONFIRMADO média** | `ContentView:26` Keychain a cada body eval; Settings `onChange` → `salvar`+`recarregar` sem debounce (`:100-114`). |
| 9 deps/CI | **CONFIRMADO média** | `python-docx` runtime só em testes; licenses sem pptx/openpyxl (lista python-docx indevido); CI `pip install` vs uv; sem gitleaks; sem job PyInstaller. |
| 10 código | **CONFIRMADO média** (nuance ContextoAssets) | Ver §Arquitetura. |
| 11 processo | **CONFIRMADO baixa** | Commit direto `4572299` uv.lock em main; PR#18 `CONFLICTING`; PR#19 `MERGEABLE`/`BLOCKED`; todo.md Fase 0–2 unchecked; README exemplos sem `converter`. |
| 12 OK segurança | **CONFIRMADO em geral** | Ver §Segurança e [KIMI]. |

---

## Achados (auditoria independente)

### Críticos

**C1. GUI não converte — mismatch CLI Typer**  
`core/cli.py:107` `@app.command() def converter` — app só tem commands `converter`/`llm`.  
`BatchProcessor.swift:116` args sem subcomando → Typer: *No such command*.  
**Evidência runtime:** exit com erro (sem subcomando); sucesso com `converter`.  
**Fix:** (a) `invoke_without_command=True` + options no callback canônico, **ou** (b) GUI prefixa `"converter"`. Preferir (a)+README alinhado. Revalidar binário com args exatos da GUI.

**C2. Pillow 12.2.0 — 13 CVEs, fix 12.3.0**  
`uv.lock` pin 12.2.0; `pyproject` `>=10.0.0`. Processa bytes de PDF/DOCX/imagens.  
**Fix:** `Pillow>=12.3.0` + `uv lock` + CI verde.

### Altos

**A1. Avisos de qualidade descartados na GUI**  
`BatchProcessor.swift:182,209-211` — violar ADR-0005.  
**Fix:** `atualizarProgresso(..., avisos: [String] = [])` e passar `item.avisos`.

### Médios

**M1. `processoAtivo` / `cancelar()` mortos** — unificar no box ou remover campo/método.  
**M2. SSRF host não filtrado (aceito c/ nuance)** — `_url` (`llm_enhancer.py:85-113`) allowlist scheme + rejeita userinfo; **não** bloqueia `169.254.169.254`/localhost. URL vem do **próprio usuário** (Settings/env). Em app desktop local, bloquear metadata é defense-in-depth opcional, não requisito crítico. Manter como está + documentar threat model em SECURITY.md.  
**M3. Baseline secrets vazio** — `detect-secrets audit` + update.  
**M4. Keychain/sandbox só no ADR** — espelhar em SECURITY.md.  
**M5. `testar` coletado pelo pytest** — `from ... import testar as _testar_llm` ou `__all__`.  
**M6. Keychain no body + Settings sem debounce**.  
**M7. `python-docx` → dev-dep**; THIRD-PARTY: +python-pptx, +openpyxl; −python-docx se não embarcado.  
**M8. CI:** uv/`uv sync --frozen`; gitleaks; job smoke PyInstaller (args GUI).  
**M9. API assets inconsistente** — `ContextoAssets` existe e é per-call (thread-safe OK). Mas `pdf_to_md`/`doc_to_md` ainda expõem ~7 params; `_montar_contexto` só em `doc_converter.py:115` com `.resolve()`; `converter.py:91-96` **não** resolve `assets_dir` na montagem (só em `preparar_assets_dir` na escrita). Latente `/var`→`/private/var`.  
**M10. `_extrair_chunks_markdown` (`converter.py:134-135`)** engole qualquer Exception → `[]` silencioso.  
**M11. Duplicações:** tabela MD (pptx + xlsx); `_MIME_PARA_EXT` vs `_EXTENSOES_SEGURAS` (papéis distintos — MIME→ext vs allowlist; consolidar se desejado).  
**M12. Código morto** `_MARGEM_PADRAO_PCT` (`converter.py:36`).

### Baixos

**B1.** Commit direto main `4572299` (viola CLAUDE.md PR-only). **[KIMI: confirmado]**  
**B2.** PR#18 dirty; PR#19 mergeable/blocked.  
**B3.** `tasks/todo.md` stale; KIMI-BRIEFING histórico.  
**B4.** CHANGELOG 0.6.0 omite Emenda 2 ADR-0005.  
**B5.** `WindowGroup("pdf2md")` + `Text("pdf2md")` header.  
**B6.** `colarImagem` I/O sync main thread (`ContentView ~370-404`).  
**B7.** `ProcessoBox: @unchecked Sendable`; `waitUntilExit` sem watchdog (`ProcessRunner.swift:58`). **[KIMI: confirmado — risco baixo: terminate no cancel; hang só se filho zumbi]**  
**B8.** README exemplos quebrados (mesma raiz de C1).  
**B9.** SECURITY.md ainda cita antiword; runtime usa textutil.

### Segurança — OK verificados

| CWE | Status | Evidência |
|-----|--------|-----------|
| CWE-22 | OK | `validar_path_seguro` (`utils.py:89`); `caminho_seguro` + regex nome (`image_assets.py:102-116`); nomes gerados em pdf_images |
| CWE-522 | OK | Key só env (`BatchProcessor:138-145`, `ProcessRunner:31-36`); CLI comenta nunca argv (`cli.py:303`) |
| CWE-209/532 | OK | `sanitizar_mensagem_erro`; `_erro_seguro` sem URL/key |
| textutil | OK | path fixo `/usr/bin/textutil`, lista args, `timeout=30`, sem shell (`doc_converter.py:290-293`) |
| Thread-safety batch | OK | `ContextoAssets`/coletor per-call; `lru_cache` tesseract_cmd/disponivel é init-once (ok); `_silenciar_stdout_nativo` só na fronteira CLI (threads unidas antes restore) |

---

## Itens [KIMI] — validação cruzada

1. **Push direto main** — **confirmado** (`4572299 chore: uv.lock` no first-parent, não-merge).  
2. **ProcessoBox / waitUntilExit / colarImagem** — **confirmados** como qualidade/robustez, não vulns. Cancel real via Task+box. Watchdog opcional (timeout LLM já no Python).  
3. **SSRF localhost/metadata** — **não exigir blocklist** no threat model atual (usuário controla URL no próprio Mac). Opcional defense-in-depth.

---

## Veredito final

| Severidade | Qtd |
|------------|-----|
| Crítica | **2** (C1 GUI/CLI, C2 Pillow) |
| Alta | **1** (A1 avisos) |
| Média | **12** |
| Baixa | **9** |

**Ação imediata:** (1) alinhar CLI↔GUI (`converter` canônico ou args GUI); (2) bump Pillow≥12.3.0; (3) propagar `avisos` no `atualizarProgresso`. Sem C1 o `.app` v0.6.0 **não converte** — qualquer feature LLM/imagens na GUI é inalcançável.

Preliminar: bem calibrada; único overstatement material = severidade de `cancelar()` (funciona via Task). Contagem Pillow: 13 únicos, não 16.
