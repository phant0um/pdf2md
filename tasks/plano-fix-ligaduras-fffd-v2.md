# Plano v2 — Correção do fix de ligaduras U+FFFD

Status: PRONTO PARA EXECUÇÃO
Substitui: `tasks/plano-fix-ligaduras-fffd.md` (v1)
Worktree: `.claude/worktrees/character-encoding-conversion-1643f9`
Base: worktree com a implementação v1 já aplicada (4 arquivos modificados, não commitados)

> **Não é um fix do zero.** `core/converter.py` da v1 está correto e NÃO deve ser
> tocado além do item 3. O defeito está confinado ao corpo de
> `reparar_ligaduras` em `core/quality.py` e aos testes que o cobrem.

---

## 1. O que deu errado na v1

### 1.1 P0 — Corrupção silenciosa de texto

A v1 aceita uma palavra do dicionário cujo sufixo é *prefixo* do sufixo real do
token, e descarta o resto. Comprovado com a implementação atual:

```
re�ected  + ref "only reflect here"  →  "reflect"    ← "ed" apagado
Work�ows  + ref "workflow"           →  "Workflow"   ← "s" apagado
```

Linha culpada (`core/quality.py`, dentro de `_reparar`):

```python
consumed = dir_len > len(resto) and dir_.startswith(resto)
if resto == dir_ or consumed:
```

Plural e flexão são universais em PT-BR e EN. Isso muda o texto do usuário sem
emitir aviso nenhum — falha pior que o bug original, porque `U+FFFD` é visível e
texto truncado não é.

### 1.2 P0 — Premissa falsa, testes fitados a ela

A v1 justificou o algoritmo complexo com: *"o FFFD ocupa 1 posição de glifo mas a
ligadura tinha 2+ chars, então `str.replace` não funciona"*.

Falso. O token real no PDF é `Work�ow`, não `Work�own`. Medição dos dois
algoritmos sobre o mesmo documento:

```
simples (plano v1): 8 reparos | complexo (entregue): 8 reparos
outputs idênticos: True
```

Os testes da v1 usam `"Work�own"` — input que não ocorre em nenhum PDF. A
especificação da v1 pedia `"Work�ow"`. O input foi alterado para casar com a
implementação, então os 8 testes verdes não provam nada sobre o comportamento real.

Bônus: `test_multiplos_fffd_mesmo_token` tem docstring "múltiplos U+FFFD" e input
com **um** FFFD.

### 1.3 P1 — Custo sem retorno

v1 varre o vocabulário inteiro (2779 palavras, reordenado a cada chamada) com loop
de posições aninhado, **por token**. Zero ganho medido sobre `O(tokens × 7)`.

### 1.4 P1 — Número de testes reportado errado

Reportado "55/55 pass". A suíte real deste worktree, após sync das extras, tem
**283 testes**. 55 é subconjunto. Sem `uv sync --all-extras` a coleta nem roda
(`ModuleNotFoundError: pptx` / `openpyxl`).

### 1.5 P2 — Achados de auditoria não absorvidos

- `getattr(pymupdf4llm, "_use_layout", False)`: API privada com default `False`.
  Renomeada no upstream → volta silenciosamente a `use_ocr=True`, perde ~40% de
  perf, sem erro e sem teste que pegue.
- Nenhum teste de integração exercita o caminho real do `pdf_to_md`.

---

## 2. Correção em `core/quality.py`

### 2.1 Substituir o corpo de `reparar_ligaduras`

Trocar a função inteira (da linha `def reparar_ligaduras` até o `return` final) por:

```python
def reparar_ligaduras(md: str, referencia: str) -> tuple[str, int]:
    """
    Repara U+FFFD originado de glifo de ligadura (fl, fi, ff...) não mapeado.

    Motivo: o engine de layout do pymupdf4llm devolve U+FFFD para glifos de
    ligadura em PDFs cuja fonte não traz ToUnicode ("Work�ow", "re�ect"),
    enquanto o `page.get_text()` clássico do PyMuPDF resolve o mesmo glifo
    corretamente. Usamos esse texto cru como DICIONÁRIO DO PRÓPRIO DOCUMENTO —
    nunca adivinhamos a expansão.

    O U+FFFD substitui a ligadura inteira, 1-para-1: `"Work�ow"` vira
    `"Workflow"` por substituição direta. Um candidato só é aceito se a palavra
    resultante existir, inteira, no dicionário. Token sem candidato válido fica
    INTACTO e continua disparando o aviso de validar_qualidade — nunca é
    truncado nem aproximado.

    Limitação conhecida: o dicionário é por-documento. Palavra cuja grafia
    correta só aparece dentro de região de imagem não tem referência no texto
    cru e não é reparada.

    Args:
        md: Markdown extraído, possivelmente com U+FFFD.
        referencia: Texto cru do mesmo documento (fonte da verdade).

    Returns:
        (md_reparado, n_tokens_reparados)
    """
    if "�" not in md or not referencia:
        return md, 0

    dicionario = {p.lower() for p in _PALAVRA_RE.findall(referencia)}
    if not dicionario:
        return md, 0

    n_total = 0

    def _reparar(m: re.Match[str]) -> str:
        nonlocal n_total
        token = m.group(0)
        letras = token.replace("�", "")
        if len(letras) < _MIN_LETRAS_REPARO:
            return token

        # Token todo em caixa alta recebe ligadura em caixa alta:
        # "WORK�OW" → "WORKFLOW", nunca "WORKflOW".
        caixa_alta = letras.isupper()

        for ligadura in _LIGADURAS:
            candidato = token.replace(
                "�", ligadura.upper() if caixa_alta else ligadura
            )
            if candidato.lower() in dicionario:
                n_total += 1
                return candidato
        return token

    return _TOKEN_FFFD_RE.sub(_reparar, md), n_total
```

Remover junto: a linha `palavras_ordenadas = sorted(...)` e seu comentário.

### 2.2 Corrigir comentário enganoso

`_LIGADURAS` hoje diz `# Ordem importa: as mais comuns primeiro (o primeiro match vence).`
A aceitação exige match exato no dicionário, então a ordem só decide em
ambiguidade real (raro). Trocar por:

```python
# Ligaduras tipográficas candidatas ao reparo de U+FFFD.
# A aceitação exige match no dicionário do documento, então a ordem só
# desempata ambiguidade genuína (ex.: "�ies" → "flies" vs "fies").
```

---

## 3. Correção em `core/converter.py`

Único ponto a mudar. Fallback público em vez de default silencioso:

```python
import importlib.util
```

```python
# pymupdf4llm >= 1.27 roda Tesseract por conta própria nas imagens embutidas
# (use_ocr=True é o default do caminho de layout). O projeto já tem OCR próprio
# controlado por ModoImagem — o do pymupdf4llm só duplica trabalho (~40% do
# tempo de extração) e produz texto pior que o embutido.
#
# `_use_layout` é privado: se o upstream renomear, caímos na detecção pública
# do módulo de layout em vez de desligar o fix em silêncio.
_LAYOUT_ATIVO: bool = getattr(
    pymupdf4llm,
    "_use_layout",
    importlib.util.find_spec("pymupdf.layout") is not None,
)
_KWARGS_EXTRACAO: dict[str, Any] = {"use_ocr": False} if _LAYOUT_ATIVO else {}
```

Nada mais em `converter.py` muda. O acumulador `bruto`, a chamada de reparo e o
aviso da v1 estão corretos.

---

## 4. Testes — `tests/test_quality.py`

**Substituir** o bloco `# ── reparar_ligaduras ──` inteiro. Inputs passam a ser os
que ocorrem de fato em PDF (`Work�ow`, não `Work�own`).

| teste | `md` | `referencia` | esperado |
|---|---|---|---|
| `repara_com_dicionario` | `Work�ow` | `Workflow data` | `Workflow`, 1 |
| `repara_ligadura_fi` | `classi�cation` | `classification` | `classification`, 1 |
| `repara_ligadura_ff` | `di�erent` | `different approach` | `different`, 1 |
| **`nao_trunca_plural`** | `Work�ows` | `workflow` | **inalterado**, 0 |
| **`nao_trunca_flexao`** | `re�ected` | `only reflect here` | **inalterado**, 0 |
| `repara_flexao_quando_ref_tem` | `re�ected` | `reflect and reflected` | `reflected`, 1 |
| `preserva_sem_match` | `Xyz�abc` | `nada a ver` | inalterado, 0 |
| `nao_toca_token_curto` | `[�]` | `fl fi texto` | inalterado, 0 |
| `noop_sem_fffd` | `texto limpo` | `referência` | inalterado, 0 |
| `noop_referencia_vazia` | `Work�ow` | `""` | inalterado, 0 |
| `caixa_alta` | `WORK�OW` | `workflow` | `WORKFLOW`, 1 |
| `caixa_mista_preservada` | `Re�ect` | `reflect` | `Reflect`, 1 |
| `acento_preservado` | `Work�ow e ação` | `Workflow e ação` | contém `Workflow` e `ação`, 1 |
| `multiplos_tokens` | `Work�ow e re�ect` | `Workflow reflect` | ambos reparados, 2 |

Os dois testes em negrito são o gate anti-regressão do P0. Sem eles a v1 volta.

Docstring de cada teste descreve o que o input **realmente** é. Nenhum teste
afirma "múltiplos U+FFFD" para input com um.

---

## 5. Testes — `tests/test_converter.py`

A v1 não exercita o caminho real. Repo gera fixtures em runtime com `fitz`
(`tests/conftest.py:11`) e não commita binário — manter a convenção.

**Novo teste de integração** (sem PDF commitado, determinístico):

```python
def test_pdf_to_md_repara_ligadura_e_avisa(tmp_path, monkeypatch):
    """pdf_to_md repara FFFD do chunk usando o texto cru da própria página."""
    import fitz
    from core import converter

    pdf = tmp_path / "ligadura.pdf"
    doc = fitz.open()
    pagina = doc.new_page()
    # >= _MIN_TEXTO_PAGINA chars para a página contar como texto nativo
    pagina.insert_text((50, 50), "Workflow reflect texto de referencia do documento")
    pagina.insert_text((50, 80), "linha extra para passar do minimo de caracteres")
    doc.save(str(pdf))
    doc.close()

    # Simula o defeito do engine de layout: chunk com FFFD no lugar da ligadura
    monkeypatch.setattr(
        converter,
        "_extrair_chunks_markdown",
        lambda path, avisos=None: [{"text": "Work�ow re�ect"}],
    )

    avisos: list[str] = []
    md = converter.pdf_to_md(pdf, avisos=avisos)

    assert "Workflow" in md
    assert "reflect" in md
    assert "�" not in md
    assert any("ligadura" in a for a in avisos)
```

**Manter** o teste de regressão byte-idêntica da v1 (fixture sem FFFD → output
inalterado). Ele já valida que `use_ocr=False` não degrada os fixtures gerados,
que são texto puro sem imagem.

---

## 6. Verificação

**Precondição obrigatória** (sem isso a coleta falha e o executor persegue fantasma):

```bash
uv sync --all-extras
```

```bash
uv run pytest tests/ -q
```
```bash
uv run ruff check core/ tests/
```

Prova empírica em PDF real:

```bash
uv run python -c "
from pathlib import Path
from core.converter import pdf_to_md
for f in ['2502.02533v2.pdf', '2608.11727v1.pdf']:
    p = Path.home() / 'Downloads' / f
    av = []
    md = pdf_to_md(p, avisos=av)
    print(f, '| FFFD:', md.count('�'), '| Workflow:', 'Workflow' in md, '|', av)
"
```

Prova anti-corrupção (o defeito da v1):

```bash
uv run python -c "
from core.quality import reparar_ligaduras as r
assert r('Work�ows', 'workflow') == ('Work�ows', 0), 'plural truncado'
assert r('re�ected', 'only reflect here') == ('re�ected', 0), 'flexao truncada'
assert r('WORK�OW', 'workflow') == ('WORKFLOW', 1), 'caixa alta'
print('anti-corrupcao OK')
"
```

### Critério de done

| item | valor exigido |
|---|---|
| suíte | **283 passed** (não 55) |
| ruff | `All checks passed!` |
| `2502.02533v2.pdf` | `FFFD: 2` · `Workflow: True` · aviso com **8** ligaduras |
| `2608.11727v1.pdf` | `FFFD: 1` · **avisos vazios** — esperado, ver §7 |
| prova anti-corrupção | `anti-corrupcao OK` |

---

## 7. Escopo honesto — o que este fix NÃO resolve

A GUI acusou 3 arquivos. Cobertura real:

| arquivo | FFFD | natureza | reparado |
|---|---|---|---|
| `2502.02533v2` | 10 | 8 ligadura + 2 fonte matemática | **8/10** |
| `2608.11727v1` | 1 | `�]` fonte matemática | **0/1** |
| `2608.11246v1` | 3 | não disponível em disco | desconhecido |

`2608.11727v1` **continuará mostrando o aviso laranja** após o fix. É o
comportamento correto: sem grafia de referência não há reparo possível, e o
guard `_MIN_LETRAS_REPARO` impede que qualquer candidato case por acidente.

Este é um fix de **ligaduras tipográficas**, não do aviso U+FFFD em geral. Dizer
o contrário ao usuário repete o erro de comunicação da v1.

FFFD de fonte matemática (`Σ`, `∫`) fica fora: o texto cru também não traz a
grafia correta (devolve `Í` para `Σ`), então não há dicionário possível.

---

## 8. Entrega

Dois commits separáveis:

```
fix: repara ligaduras perdidas (U+FFFD) via dicionário do próprio PDF
perf: desliga OCR interno do pymupdf4llm (-40% na extração)
```

- `CHANGELOG.md` — seção Unreleased. Descrever como fix de **ligaduras**, com a
  limitação do §7 explícita.
- `tasks/lessons.md` — entrada nova (max 30, consolidar antes de adicionar):
  *"Teste cujo input foi alterado para casar com a implementação não prova nada.
  Quando o código diverge da spec, validar a premissa contra o dado real antes de
  reescrever o algoritmo."*
- Remover `tasks/plano-fix-ligaduras-fffd.md` (v1) — substituído por este.
- PR para `main`, sem push direto.

## 9. Fora de escopo

- U+FFFD de fonte matemática
- `doc_converter.py`, `xlsx_converter.py`, `pptx_converter.py` — não usam o engine
  de layout do pymupdf4llm
- Reescrita do pipeline de qualidade
- Fixture PDF commitado — viola a convenção de fixtures em runtime do repo
