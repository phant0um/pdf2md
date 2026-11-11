"""
LLM enhancer — fallback de qualidade via modelo local (Ollama) ou remoto.

Provider-agnostic: usa API compatível com OpenAI via urllib.request (stdlib,
sem deps extras). Funciona com Ollama, Gemini, OpenRouter, Groq etc.

Configuração por precedência: flag CLI (ConfigLLM) > variável de ambiente > default:

  PDF2MD_LLM_URL    URL base da API  (default: http://localhost:11434/v1 = Ollama)
  PDF2MD_LLM_KEY    API key          (default: "ollama" — Ollama não valida)
  PDF2MD_LLM_MODEL  Modelo           (default: llama3.2-vision)
  PDF2MD_LLM_TIMEOUT Timeout em seg  (default: 120)

Exemplos de configuração:
  # Ollama local (padrão — grátis, offline, privado)
  PDF2MD_LLM_URL=http://localhost:11434/v1
  PDF2MD_LLM_MODEL=llama3.2-vision

  # Gemini Flash (grátis com limite, excelente qualidade)
  PDF2MD_LLM_URL=https://generativelanguage.googleapis.com/v1beta/openai/
  PDF2MD_LLM_KEY=sua_chave_do_aistudio
  PDF2MD_LLM_MODEL=gemini-2.0-flash

  # Groq (ultra rápido, grátis, sem visão — só texto)
  PDF2MD_LLM_URL=https://api.groq.com/openai/v1
  PDF2MD_LLM_KEY=sua_chave_groq
  PDF2MD_LLM_MODEL=llama-3.1-8b-instant

  # OpenCode Zen (open-source models hospedados)
  PDF2MD_LLM_URL=https://opencode.ai/zen/v1
  PDF2MD_LLM_KEY=sua_chave_opencode
  PDF2MD_LLM_MODEL=deepseek-v4-flash

  # OpenCode Go (modelos fechados: grok, kimi, deepseek)
  PDF2MD_LLM_URL=https://opencode.ai/zen/go/v1
  PDF2MD_LLM_KEY=sua_chave_opencode
  PDF2MD_LLM_MODEL=grok-4.5

  # Ollama Cloud (modelos hospedados pela Ollama)
  PDF2MD_LLM_URL=https://ollama.com/v1
  PDF2MD_LLM_KEY=sua_chave_ollama_cloud
  PDF2MD_LLM_MODEL=gpt-oss:120b
"""

import base64
import bisect
import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, TypedDict
from urllib.parse import urlsplit

from core.utils import _MAX_BYTES_IMAGEM

# ── Configuração ──────────────────────────────────────────────────────────────

_URL_PADRAO = "http://localhost:11434/v1"
_MODELO_PADRAO = "llama3.2-vision"
_TIMEOUT_PADRAO = 120
_MAX_CHARS_LLM = 12000       # ~3k tokens por bloco — cabe no contexto do modelo
_MAX_BLOCOS_LLM = 20         # ~240k chars; limita chamadas/custo em documentos enormes
_MAX_TOKENS_RESPOSTA = 4096
_TIMEOUT_VERIFICACAO = 2     # segundos — timeout rápido para checagem de disponibilidade
_TIMEOUT_MODELOS = 10        # segundos — timeout para listagem de modelos

# Schemes aceitos para a URL do LLM — bloqueia file://, ftp://, gopher:// etc.
# que permitiriam SSRF/leitura arbitrária de arquivo via urlopen (CWE-918).
_SCHEMES_PERMITIDOS = frozenset({"http", "https"})


@dataclass(frozen=True)
class ConfigLLM:
    """
    Configuração do LLM com precedência flag > env > default.

    Campos None = não configurado pela flag → cai no env (ou no default).
    A API key nunca deve vir de argv: chega via environment (GUI) ou
    PDF2MD_LLM_KEY — argv aparece em `ps aux` (CWE-522).
    """

    url: str | None = None
    modelo: str | None = None
    key: str | None = None


class RespostaTruncadaError(RuntimeError):
    """O LLM parou no limite de max_tokens — o texto devolvido está incompleto."""


class ModeloLLMInfo(TypedDict):
    """Item da lista de modelos — id do modelo + capacidade de visão."""
    id: str
    visao: bool | None


class ResultadoTeste(TypedDict):
    """Resultado do probe de conexão — campos sempre presentes no JSON."""
    ok: bool
    latencia_ms: int | None
    erro: str | None


def _url(config: ConfigLLM | None = None) -> str:
    """
    Resolve a URL do LLM (flag > env > default) e valida.

    Aplica allowlist de scheme (http/https) e rejeita credenciais embutidas
    na URL (user:pass@host) — mitigação de SSRF (CWE-918) e de leak de
    credenciais em logs/erros (CWE-209/532).

    Returns:
        URL validada, sem barra final.

    Raises:
        ValueError: Se scheme não permitido, host vazio ou houver userinfo.
    """
    if config is not None and config.url:
        bruta = config.url
    else:
        bruta = os.getenv("PDF2MD_LLM_URL", _URL_PADRAO)
    bruta = bruta.rstrip("/")
    partes = urlsplit(bruta)

    if partes.scheme not in _SCHEMES_PERMITIDOS:
        raise ValueError("PDF2MD_LLM_URL deve usar http ou https")
    if not partes.hostname:
        raise ValueError("PDF2MD_LLM_URL deve conter um host válido")
    if partes.username:
        raise ValueError("credenciais não devem ser embutidas na URL")

    return bruta


def _modelo(config: ConfigLLM | None = None) -> str:
    if config is not None and config.modelo:
        return config.modelo
    return os.getenv("PDF2MD_LLM_MODEL", _MODELO_PADRAO)


def _key(config: ConfigLLM | None = None) -> str:
    if config is not None and config.key:
        return config.key
    return os.getenv("PDF2MD_LLM_KEY", "ollama")


def _timeout() -> int:
    try:
        return int(os.getenv("PDF2MD_LLM_TIMEOUT", str(_TIMEOUT_PADRAO)))
    except ValueError:
        return _TIMEOUT_PADRAO


# ── Prompts ───────────────────────────────────────────────────────────────────

_PROMPT_MELHORIA = """\
O texto abaixo foi extraído automaticamente de '{nome}'.
Corrija os problemas de qualidade sem alterar o conteúdo:
- Junte palavras quebradas por hifenização (ex: "pala-\nvra" → "palavra")
- Corrija acentuação PT-BR quando evidente (ã, ç, é, ó, á, etc.)
- Preserve estrutura Markdown (headings, listas, tabelas, código)
- NÃO adicione nem remova informação — só corrija forma

Retorne APENAS o Markdown corrigido, sem explicação ou comentários.

{md}"""

_PROMPT_OCR = """\
Transcreva todo o texto visível nesta imagem para Markdown.
- Preserve estrutura: títulos, listas, tabelas, parágrafos
- Corrija acentuação PT-BR quando evidente
- Se houver tabela, use formato Markdown de tabela
- Retorne APENAS o Markdown, sem explicação"""

# ── Cliente HTTP (stdlib — sem deps extras) ───────────────────────────────────

_HEADERS_BASE = {"Content-Type": "application/json"}


def _cabecalhos_auth(config: ConfigLLM | None) -> dict[str, str]:
    """Cabeçalho de autorização — a key viaja no header, nunca em argv/log."""
    return {**_HEADERS_BASE, "Authorization": f"Bearer {_key(config)}"}


def _completar(mensagens: list[dict[str, Any]], config: ConfigLLM | None = None) -> str:
    """
    Chama /chat/completions via urllib.request (sem deps extras).
    Compatível com qualquer API no formato OpenAI.
    """
    payload = json.dumps({
        "model": _modelo(config),
        "messages": mensagens,
        "max_tokens": _MAX_TOKENS_RESPOSTA,
        "temperature": 0.1,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{_url(config)}/chat/completions",
        data=payload,
        headers=_cabecalhos_auth(config),
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=_timeout()) as resp:
        data = json.loads(resp.read().decode("utf-8"))

    escolha = data["choices"][0]
    # finish_reason "length" = resposta cortada em _MAX_TOKENS_RESPOSTA. Usar
    # o texto parcial apagaria o fim do documento em silêncio — o chamador
    # captura a exceção e mantém o original com aviso.
    if escolha.get("finish_reason") == "length":
        raise RespostaTruncadaError("resposta do LLM truncada em max_tokens")
    conteudo = escolha["message"]["content"]
    if not isinstance(conteudo, str):
        # Não usa assert: some sob `python -O` e o contrato exige str.
        raise TypeError("resposta do LLM em formato inesperado")
    return conteudo


def _erro_seguro(exc: Exception) -> str:
    """
    Mensagem de erro sem detalhes sensíveis (CWE-209/532).

    Não inclui URL, credencial, path do ambiente nem a mensagem bruta da
    exceção — só código HTTP ou categoria do problema.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    if isinstance(exc, TimeoutError):
        return "timeout"
    if isinstance(exc, urllib.error.URLError):
        return "servidor inacessível"
    return type(exc).__name__


def _get_models(config: ConfigLLM | None, timeout: int) -> dict[str, Any] | None:
    """
    GET {url}/models com auth — shape único compartilhado por
    disponivel()/testar()/listar_modelos() (sem triplicação de Request).
    Devolve o JSON do endpoint ou None se a resposta não for dict.
    """
    req = urllib.request.Request(
        f"{_url(config)}/models", method="GET", headers=_cabecalhos_auth(config)
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        dados = json.loads(resp.read().decode("utf-8"))
    return dados if isinstance(dados, dict) else None


# ── API pública ───────────────────────────────────────────────────────────────

@lru_cache(maxsize=1)
def disponivel(config: ConfigLLM | None = None) -> bool:
    """
    Verifica se o endpoint LLM está acessível (timeout rápido de 2s).

    Resultado é cacheado (lru_cache) — só testa uma vez por processo,
    evitando latência em cada arquivo do batch.

    Returns:
        True se o endpoint respondeu. False se não configurado ou inacessível.
    """
    try:
        url = _url(config)
    except ValueError:
        # URL malformada/insegura — trata como indisponível, não propaga.
        return False
    # Se a URL efetiva é o default e nada a configurou explicitamente
    # (nem flag, nem env), não testa — evita timeout em máquinas sem Ollama.
    url_explicita = (config.url if config is not None else None) or os.getenv("PDF2MD_LLM_URL")
    if url == _URL_PADRAO and url_explicita is None:
        return False
    try:
        return _get_models(config, _TIMEOUT_VERIFICACAO) is not None
    except Exception:
        return False


def testar(config: ConfigLLM | None = None) -> ResultadoTeste:
    """
    Prova de disponibilidade SEM cache, com latência medida.

    Uso: subcomando `pdf2md llm testar --json` (GUI) e diagnóstico manual.

    Returns:
        {"ok": bool, "latencia_ms": int | None, "erro": str | None}
        — "erro" é mensagem segura (CWE-209), nunca a exceção bruta.
    """
    try:
        _url(config)  # valida antes de medir (SSRF/CWE-918)
    except ValueError as exc:
        return {"ok": False, "latencia_ms": None, "erro": str(exc)}

    inicio = time.perf_counter()
    try:
        dados = _get_models(config, _TIMEOUT_VERIFICACAO)
        latencia_ms = int((time.perf_counter() - inicio) * 1000)
        if dados is None:
            return {"ok": False, "latencia_ms": None, "erro": "resposta inesperada"}
        return {"ok": True, "latencia_ms": latencia_ms, "erro": None}
    except Exception as exc:
        return {"ok": False, "latencia_ms": None, "erro": _erro_seguro(exc)}


def _base_ollama(url: str) -> str | None:
    """Deriva a base da API nativa do Ollama da URL OpenAI-compat.

    "http://localhost:11434/v1" → "http://localhost:11434".
    Só para hosts locais — não assume API nativa em servidor remoto.
    """
    host = urlsplit(url).hostname or ""
    if host not in ("localhost", "127.0.0.1") or not url.endswith("/v1"):
        return None
    return url[:-3]


def _visao_modelo(modelo_id: str, url: str) -> bool | None:
    """
    Detecta se o modelo tem visão (para o picker da GUI).

    Ollama local: `GET /api/show` expõe a flag `vision` no model_info.
    Outros providers: sem fonte confiável → None (desconhecido); o preset
    do provider na GUI cobre o padrão (ex: Groq = sem visão).
    """
    base = _base_ollama(url)
    if base is None:
        return None
    try:
        payload = json.dumps({"name": modelo_id}).encode("utf-8")
        req = urllib.request.Request(
            f"{base}/api/show",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT_MODELOS) as resp:
            info = json.loads(resp.read().decode("utf-8"))
        mi = info.get("model_info") or {}
        if "vision" in mi:
            return bool(mi["vision"])
        return bool(info["vision"]) if "vision" in info else None
    except Exception:
        return None


def listar_modelos(
    config: ConfigLLM | None = None,
) -> tuple[list[ModeloLLMInfo] | None, str | None]:
    """
    Lista os modelos disponíveis no endpoint (GET /models).

    Cada item: {"id": str, "visao": bool | None} — "visao" é True/False
    quando detectável (Ollama local) e None quando desconhecido.

    Returns:
        (modelos, erro) — modelos é None se a chamada falhou; "erro" é
        mensagem segura (CWE-209), nunca a exceção bruta.
    """
    try:
        url = _url(config)
    except ValueError as exc:
        return None, str(exc)

    try:
        dados = _get_models(config, _TIMEOUT_MODELOS)
        if dados is None:
            return None, "resposta inesperada"
        ids = sorted(
            item["id"] for item in dados.get("data", []) if item.get("id")
        )
        modelos = [ModeloLLMInfo(id=i, visao=_visao_modelo(i, url)) for i in ids]
        return modelos, None
    except Exception as exc:
        return None, _erro_seguro(exc)


def melhorar_markdown(
    md: str, origem: Path, config: ConfigLLM | None = None
) -> tuple[str, list[str]]:
    """
    Melhora qualidade do Markdown via LLM (pós-processamento de texto).

    Útil quando o conversor produziu texto com palavras quebradas, acentos
    corrompidos ou formatação perdida.

    Documentos acima de `_MAX_CHARS_LLM` vão ao LLM em blocos cortados entre
    parágrafos (fora de blocos de código), no máximo `_MAX_BLOCOS_LLM` blocos;
    o resto fica sem melhoria, com aviso. Se um bloco falha, ele mantém o texto
    original e os demais continuam melhorados. Se todos os blocos enviados
    falham, devolve o `md` original. As quebras de linha originais nas bordas de
    cada bloco são preservadas (não se insere linha em branco num corte simples).

    Falha graciosamente: se o LLM não estiver disponível ou retornar erro,
    retorna o markdown original com um aviso — nunca bloqueia a conversão.

    Args:
        md: Markdown bruto (já limpo por quality.py).
        origem: Path do arquivo de origem (para contexto no prompt).
        config: ConfigLLM opcional (flag > env > default).

    Returns:
        (md_melhorado, avisos_adicionais) — avisos_adicionais é [] se OK.
    """
    blocos = _dividir_blocos(md, _MAX_CHARS_LLM)

    if len(blocos) == 1:
        prompt = _PROMPT_MELHORIA.format(nome=origem.name, md=md)
        try:
            resultado = _completar([{"role": "user", "content": prompt}], config)
            return resultado, []
        except Exception as exc:
            # Não inclui str(exc) no aviso — pode conter URL, credencial ou path
            # do ambiente (CWE-209/532). Só o nome do tipo é exposto.
            return md, [f"LLM enhancement falhou ({type(exc).__name__})"]

    enviados = blocos[:_MAX_BLOCOS_LLM]
    partes: list[str] = []
    tipos_falha: list[str] = []
    for bloco in enviados:
        prompt = _PROMPT_MELHORIA.format(nome=origem.name, md=bloco)
        try:
            melhorado = _completar([{"role": "user", "content": prompt}], config)
        except Exception as exc:
            # Bloco que falha mantém o texto original; os demais seguem.
            partes.append(bloco)
            tipos_falha.append(type(exc).__name__)
            continue
        # Preserva as quebras de borda ORIGINAIS do bloco: um corte após um único
        # "\n" (dentro de uma tabela, por exemplo) não pode virar linha em branco.
        inicio = bloco[: len(bloco) - len(bloco.lstrip("\n"))]
        fim = bloco[len(bloco.rstrip("\n")):]
        partes.append(inicio + melhorado.strip("\n") + fim)

    avisos: list[str] = []
    n = len(enviados)
    if tipos_falha:
        tipos = ", ".join(sorted(set(tipos_falha)))
        avisos.append(f"LLM enhancement falhou em {len(tipos_falha)} de {n} blocos ({tipos})")
    if len(blocos) > _MAX_BLOCOS_LLM:
        avisos.append(
            f"LLM aplicado só aos primeiros {_MAX_BLOCOS_LLM} blocos; "
            "o resto do documento ficou sem melhoria"
        )

    # Blocos com falha ou além do teto entram como estão; se todos falharam,
    # a junção reproduz `md` exatamente.
    partes.extend(blocos[n:])
    return "".join(partes), avisos


def _dividir_blocos(md: str, limite: int) -> list[str]:
    """
    Corta o Markdown em blocos de no máximo `limite` caracteres.

    A concatenação dos blocos é exatamente `md`. Para cada bloco que começa em
    `i`, o corte vai para a MAIOR posição `p` com `i < p <= i + limite`, por
    prioridade: (1) após uma linha em branco fora de bloco de código; (2) após
    qualquer `\n`; (3) corte seco em `i + limite`.

    Blocos de código (linhas que começam com ``` ou ~~~) são rastreados uma
    única vez sobre o texto inteiro, para não cortar dentro de uma cerca.
    """
    n = len(md)
    if n <= limite:
        return [md]

    # Uma varredura linear coleta as posições candidatas, já em ordem crescente.
    cortes_branco: list[int] = []
    cortes_quebra: list[int] = []
    em_cerca = False
    inicio = 0
    while inicio < n:
        fim = md.find("\n", inicio)
        fim = n if fim == -1 else fim + 1
        linha = md[inicio:fim]
        if linha.lstrip().startswith(("```", "~~~")):
            em_cerca = not em_cerca
        if linha.endswith("\n"):
            cortes_quebra.append(fim)
            if linha.strip() == "" and not em_cerca:
                cortes_branco.append(fim)
        inicio = fim

    blocos: list[str] = []
    i = 0
    while n - i > limite:
        teto = i + limite
        p = teto  # prioridade 3: corte seco
        for candidatos in (cortes_branco, cortes_quebra):
            k = bisect.bisect_right(candidatos, teto) - 1
            if k >= 0 and candidatos[k] > i:
                p = candidatos[k]
                break
        blocos.append(md[i:p])
        i = p
    blocos.append(md[i:])
    return blocos


def ocr_com_visao(
    imagem_path: Path, config: ConfigLLM | None = None
) -> tuple[str, list[str]]:
    """
    Transcreve imagem usando visão do LLM (OCR fallback).

    Útil quando pytesseract falha ou retorna texto muito curto
    (ex: imagens escaneadas com fontes incomuns, manuscrito, etc.).

    Falha graciosamente: retorna string vazia + aviso se LLM indisponível.

    Args:
        imagem_path: Path para arquivo de imagem (PNG recomendado).
        config: ConfigLLM opcional (flag > env > default).

    Returns:
        (texto_md, avisos) — avisos é [] se OCR com visão foi bem-sucedido.
    """
    if not imagem_path.exists():
        return "", [f"Imagem não encontrada: {imagem_path.name}"]

    try:
        # FIX 8: verifica o tamanho ANTES de ler/base64 — um render 300 dpi
        # chega a ~35 MB e uma imagem gigante estouraria memória (read_bytes)
        # e a janela de contexto do LLM. Acima do limite: falha graciosa com
        # aviso (mesmo _MAX_BYTES_IMAGEM dos assets, 50 MB).
        if imagem_path.stat().st_size > _MAX_BYTES_IMAGEM:
            return "", [
                f"imagem {imagem_path.name} excede "
                f"{_MAX_BYTES_IMAGEM // (1024 * 1024)} MB — OCR com visão ignorado"
            ]
        img_b64 = base64.standard_b64encode(imagem_path.read_bytes()).decode("ascii")

        mensagem_visao = {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                },
                {"type": "text", "text": _PROMPT_OCR},
            ],
        }

        resultado = _completar([mensagem_visao], config)
        return resultado, []
    except Exception as exc:
        # Idem: não inclui str(exc) no aviso (CWE-209/532).
        return "", [f"OCR com visão falhou ({type(exc).__name__})"]
