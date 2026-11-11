"""
Testes para core/llm_enhancer.py.
Todos os testes são mockados — nenhum requer Ollama ou API key real.
"""
import json
import os
from unittest.mock import MagicMock, patch

import pytest

from core.llm_enhancer import (
    ConfigLLM,
    _erro_seguro,
    _url,
    disponivel,
    listar_modelos,
    melhorar_markdown,
    ocr_com_visao,
)
from core.llm_enhancer import (
    # Alias com underscore: pytest coleta funções que começam com "test" —
    # o import direto de `testar` virava um teste-fantasma que fazia chamada
    # de rede real a localhost:11434 durante a suíte.
    testar as _testar_llm,
)

# ── Helper: mock de resposta HTTP ────────────────────────────────────────────

def _mock_response(conteudo: str) -> MagicMock:
    """Cria mock de urllib response que retorna JSON compatível com OpenAI."""
    body = json.dumps({
        "choices": [{"message": {"content": conteudo}}]
    }).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


# ── _url() — validação de scheme/host/credenciais (CWE-918, CWE-209/532) ────

def test_url_rejeita_scheme_file():
    """file:// permitiria leitura arbitrária de arquivo via urlopen (SSRF)."""
    with (
        patch.dict("os.environ", {"PDF2MD_LLM_URL": "file:///etc/passwd"}),
        pytest.raises(ValueError, match="http ou https"),
    ):
        _url()


def test_url_rejeita_scheme_ftp():
    """Qualquer scheme fora da allowlist {http, https} deve ser rejeitado."""
    with (
        patch.dict("os.environ", {"PDF2MD_LLM_URL": "ftp://exemplo.com/v1"}),
        pytest.raises(ValueError, match="http ou https"),
    ):
        _url()


def test_url_rejeita_scheme_gopher():
    """gopher:// é um vetor SSRF clássico — deve ser bloqueado."""
    with (
        patch.dict("os.environ", {"PDF2MD_LLM_URL": "gopher://exemplo.com/v1"}),
        pytest.raises(ValueError, match="http ou https"),
    ):
        _url()


def test_url_rejeita_credenciais_embutidas():
    """user:senha@host expõe segredo em logs/erros — rejeitado na origem."""
    with (
        patch.dict("os.environ", {"PDF2MD_LLM_URL": "https://user:chave@exemplo.com/v1"}),
        pytest.raises(ValueError, match="credenciais"),
    ):
        _url()


def test_url_rejeita_host_vazio():
    """URL sem host (ex: scheme isolado) deve ser rejeitada."""
    with (
        patch.dict("os.environ", {"PDF2MD_LLM_URL": "http:///v1"}),
        pytest.raises(ValueError, match="host"),
    ):
        _url()


def test_url_aceita_http_valido():
    """http:// com host válido é aceito normalmente."""
    with patch.dict("os.environ", {"PDF2MD_LLM_URL": "http://localhost:11434/v1"}):
        assert _url() == "http://localhost:11434/v1"


def test_url_aceita_https_valido():
    """https:// com host válido é aceito normalmente."""
    url = "https://api.groq.com/openai/v1"
    with patch.dict("os.environ", {"PDF2MD_LLM_URL": url}):
        assert _url() == url


def test_disponivel_false_quando_url_invalida():
    """disponivel() não propaga ValueError — trata URL insegura como indisponível."""
    disponivel.cache_clear()
    with patch.dict("os.environ", {"PDF2MD_LLM_URL": "file:///etc/passwd"}):
        assert disponivel() is False
    disponivel.cache_clear()


# ── avisos de erro não vazam detalhes (CWE-209/532) ──────────────────────────

def test_melhorar_markdown_aviso_nao_expoe_detalhe_da_excecao(tmp_path):
    """Aviso de falha contém só o nome do tipo da exceção, não sua mensagem."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    segredo = "Bearer sk-segredo-supersecreto-12345"

    with patch("urllib.request.urlopen", side_effect=OSError(segredo)):
        _, avisos = melhorar_markdown("texto", f)

    assert len(avisos) == 1
    assert segredo not in avisos[0]
    assert "OSError" in avisos[0]


def test_ocr_com_visao_aviso_nao_expoe_detalhe_da_excecao(tmp_path):
    """Aviso de falha de OCR contém só o nome do tipo, não a mensagem original."""
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    segredo = "token=abc123secreto"

    with patch("urllib.request.urlopen", side_effect=OSError(segredo)):
        _, avisos = ocr_com_visao(img)

    assert len(avisos) == 1
    assert segredo not in avisos[0]
    assert "OSError" in avisos[0]


# ── disponivel() ─────────────────────────────────────────────────────────────

def test_disponivel_false_sem_env():
    """Retorna False quando PDF2MD_LLM_URL não está definido (default Ollama)."""
    disponivel.cache_clear()
    with patch.dict("os.environ", {}, clear=False):
        # Remove variável se existir
        import os
        os.environ.pop("PDF2MD_LLM_URL", None)
        assert disponivel() is False
    disponivel.cache_clear()


def test_disponivel_true_quando_endpoint_responde():
    """Retorna True quando endpoint acessível (GET /models com JSON válido)."""
    disponivel.cache_clear()
    mock_resp = _mock_models_response([])
    with (
        patch.dict("os.environ", {"PDF2MD_LLM_URL": "http://localhost:11434/v1"}),
        patch("urllib.request.urlopen", return_value=mock_resp),
    ):
        assert disponivel() is True
    disponivel.cache_clear()


def test_disponivel_false_quando_endpoint_inacessivel():
    """Retorna False quando conexão recusada."""
    disponivel.cache_clear()
    with (
        patch.dict("os.environ", {"PDF2MD_LLM_URL": "http://localhost:19999/v1"}),
        patch("urllib.request.urlopen", side_effect=OSError("Connection refused")),
    ):
        assert disponivel() is False
    disponivel.cache_clear()


# ── melhorar_markdown() ───────────────────────────────────────────────────────

def test_melhorar_markdown_retorna_texto_melhorado(tmp_path):
    """Retorna texto do LLM quando chamada bem-sucedida."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    md_original = "pala­vra quebrada e integraÃ§Ã£o"
    md_esperado = "palavra quebrada e integração"

    with patch("urllib.request.urlopen", return_value=_mock_response(md_esperado)):
        resultado, avisos = melhorar_markdown(md_original, f)

    assert resultado == md_esperado
    assert avisos == []


def test_melhorar_markdown_retorna_original_em_falha(tmp_path):
    """Retorna markdown original + aviso quando LLM falha."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    md_original = "texto com problema"

    with patch("urllib.request.urlopen", side_effect=OSError("Connection refused")):
        resultado, avisos = melhorar_markdown(md_original, f)

    assert resultado == md_original  # original preservado
    assert len(avisos) == 1
    assert "falhou" in avisos[0].lower()


def test_melhorar_markdown_trunca_input_longo(tmp_path):
    """Input longo (>12000 chars) é truncado antes de enviar ao LLM."""
    f = tmp_path / "grande.pdf"
    f.write_bytes(b"x")
    md_longo = "palavra " * 5000  # ~40000 chars

    chamadas = []

    def capturar_urlopen(req, timeout=None):
        chamadas.append(req.data)
        return _mock_response("resultado")

    with patch("urllib.request.urlopen", side_effect=capturar_urlopen):
        melhorar_markdown(md_longo, f)

    # Verifica que o payload enviado é menor que o original
    assert len(chamadas) == 1
    payload_str = chamadas[0].decode("utf-8")
    assert len(payload_str) < len(md_longo)


def test_melhorar_markdown_junta_restante_apos_truncamento(tmp_path):
    """Tail do documento (>12000 chars) é preservada no output."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    cabeca = "início " * 2000    # ~14000 chars (acima do threshold)
    cauda = "\n\nFim importante"
    md_longo = cabeca + cauda

    with patch("urllib.request.urlopen", return_value=_mock_response("cabeça melhorada")):
        resultado, _ = melhorar_markdown(md_longo, f)

    assert "Fim importante" in resultado  # cauda preservada


# ── ocr_com_visao() ───────────────────────────────────────────────────────────

def test_ocr_com_visao_retorna_texto(tmp_path):
    """Retorna transcrição do LLM para imagem válida."""
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)  # PNG fake válido

    with patch("urllib.request.urlopen", return_value=_mock_response("# Título\n\nTexto transcrito.")):
        resultado, avisos = ocr_com_visao(img)

    assert "Título" in resultado
    assert avisos == []


def test_ocr_com_visao_retorna_vazio_em_falha(tmp_path):
    """Retorna string vazia + aviso quando LLM falha."""
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)

    with patch("urllib.request.urlopen", side_effect=OSError("timeout")):
        resultado, avisos = ocr_com_visao(img)

    assert resultado == ""
    assert len(avisos) == 1
    assert "falhou" in avisos[0].lower()


def test_ocr_com_visao_arquivo_inexistente(tmp_path):
    """Retorna vazio + aviso para arquivo inexistente (sem chamar LLM)."""
    resultado, avisos = ocr_com_visao(tmp_path / "nao_existe.png")

    assert resultado == ""
    assert len(avisos) == 1
    assert "não encontrada" in avisos[0].lower()


def test_ocr_com_visao_encoda_imagem_em_base64(tmp_path):
    """Imagem é codificada como base64 no payload."""
    img = tmp_path / "scan.png"
    conteudo = b"\x89PNG\r\n\x1a\n" + b"DADOS_IMAGEM" * 10
    img.write_bytes(conteudo)

    payloads = []

    def capturar(req, timeout=None):
        payloads.append(req.data.decode("utf-8"))
        return _mock_response("texto")

    with patch("urllib.request.urlopen", side_effect=capturar):
        ocr_com_visao(img)

    import base64
    b64_esperado = base64.standard_b64encode(conteudo).decode("ascii")
    assert b64_esperado in payloads[0]


# ── ConfigLLM: precedência flag > env > default (T8) ─────────────────────────

def test_url_config_precedencia_sobre_env():
    """ConfigLLM.url vence PDF2MD_LLM_URL (flag > env)."""
    with patch.dict("os.environ", {"PDF2MD_LLM_URL": "https://api.exemplo.com/v1"}):
        assert _url(ConfigLLM(url="http://localhost:11434/v1")) == "http://localhost:11434/v1"


def test_url_config_vazio_cai_no_env():
    """ConfigLLM() sem url preserva o comportamento env-only."""
    with patch.dict("os.environ", {"PDF2MD_LLM_URL": "https://api.exemplo.com/v1"}):
        assert _url(ConfigLLM(modelo="x")) == "https://api.exemplo.com/v1"
        assert _url() == "https://api.exemplo.com/v1"


def test_url_sem_env_nem_config_usa_default():
    """Sem env e sem config → default Ollama."""
    with patch.dict("os.environ", {}, clear=True):
        assert _url() == "http://localhost:11434/v1"


def test_config_url_insegura_rejeitada():
    """ConfigLLM.url passa pela MESMA validação que o env (SSRF)."""
    with pytest.raises(ValueError, match="http ou https"):
        _url(ConfigLLM(url="file:///etc/passwd"))


def test_melhorar_markdown_usa_url_e_modelo_do_config(tmp_path):
    """URL e modelo do ConfigLLM chegam à requisição real."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")

    requisicoes = []

    def capturar(req, timeout=None):
        requisicoes.append(req)
        return _mock_response("melhorado")

    config = ConfigLLM(url="http://localhost:11434/v1", modelo="llama3.2-vision")
    with (
        patch.dict("os.environ", {"PDF2MD_LLM_URL": "https://api.exemplo.com/v1"}),
        patch("urllib.request.urlopen", side_effect=capturar),
    ):
        melhorar_markdown("texto", f, config)

    assert len(requisicoes) == 1
    assert requisicoes[0].full_url == "http://localhost:11434/v1/chat/completions"
    payload = json.loads(requisicoes[0].data.decode("utf-8"))
    assert payload["model"] == "llama3.2-vision"


def test_melhorar_markdown_usa_key_do_config(tmp_path):
    """Key do ConfigLLM vai no header Authorization, nunca na URL."""
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")

    requisicoes = []

    def capturar(req, timeout=None):
        requisicoes.append(req)
        return _mock_response("melhorado")

    config = ConfigLLM(url="https://api.exemplo.com/v1", key="sk-segredo-123")
    with patch("urllib.request.urlopen", side_effect=capturar):
        melhorar_markdown("texto", f, config)

    assert requisicoes[0].full_url.startswith("https://api.exemplo.com/")
    assert "sk-segredo-123" not in requisicoes[0].full_url
    assert requisicoes[0].get_header("Authorization") == "Bearer sk-segredo-123"


def test_disponivel_probe_com_config_mesmo_sem_env():
    """Config explícito força o probe mesmo com URL default e env ausente.

    É exatamente o cenário da GUI: o toggle liga e a config chega via
    ConfigLLM — sem isto, disponivel() retornava False sem testar (bug
    do no-op silencioso no .app).
    """
    disponivel.cache_clear()
    with (
        patch.dict("os.environ", {}, clear=True),
        patch("urllib.request.urlopen", return_value=_mock_models_response([])),
    ):
        assert disponivel(ConfigLLM(url="http://localhost:11434/v1")) is True
    disponivel.cache_clear()


# ── listar_modelos() / testar() (T9) ─────────────────────────────────────────

def _mock_models_response(modelos: list[str]) -> MagicMock:
    """Mock de resposta de GET /models no formato OpenAI."""
    body = json.dumps({"data": [{"id": m, "object": "model"} for m in modelos]}).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = body
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_listar_modelos_retorna_ids_ordenados():
    """Lista ids do GET /models, ordenados."""
    with patch("urllib.request.urlopen", return_value=_mock_models_response(["b", "a", "c"])):
        modelos, erro = listar_modelos()

    assert erro is None
    assert [m["id"] for m in modelos] == ["a", "b", "c"]


def test_listar_modelos_erro_seguro_nao_vaza_detalhe():
    """Falha retorna mensagem segura — sem URL, sem key (CWE-209)."""
    config = ConfigLLM(url="http://localhost:11434/v1", key="sk-supersecreta-42")
    with patch("urllib.request.urlopen", side_effect=OSError("Connection refused /tmp/x")):
        modelos, erro = listar_modelos(config)

    assert modelos is None
    assert erro == "OSError"
    assert "sk-supersecreta-42" not in erro


def test_listar_modelos_http_401_mensagem_segura():
    """HTTPError vira 'HTTP <código>' — sem corpo de erro do servidor."""
    exc = __import__("urllib.error", fromlist=["HTTPError"]).HTTPError(
        "http://x", 401, "Unauthorized", {}, None
    )
    with patch("urllib.request.urlopen", side_effect=exc):
        modelos, erro = listar_modelos()

    assert modelos is None
    assert erro == "HTTP 401"


def test_listar_modelos_visao_ollama_local():
    """Ollama local: flag vision vinda de /api/show é reportada."""
    chamadas = []

    def responder(req, timeout=None):
        chamadas.append(req)
        if req.full_url.endswith("/models"):
            return _mock_models_response(["llama3.2-vision"])
        # /api/show
        body = json.dumps({"model_info": {"vision": True}}).encode("utf-8")
        resp = MagicMock()
        resp.read.return_value = body
        resp.__enter__ = lambda s: s
        resp.__exit__ = MagicMock(return_value=False)
        return resp

    config = ConfigLLM(url="http://localhost:11434/v1")
    with patch("urllib.request.urlopen", side_effect=responder):
        modelos, erro = listar_modelos(config)

    assert erro is None
    assert modelos == [{"id": "llama3.2-vision", "visao": True}]
    assert any(r.full_url == "http://localhost:11434/api/show" for r in chamadas)


def test_listar_modelos_visao_desconhecida_provider_remoto():
    """Provider remoto: visao é None (sem /api/show)."""
    config = ConfigLLM(url="https://api.groq.com/openai/v1")
    with patch("urllib.request.urlopen", return_value=_mock_models_response(["llama-3.1-8b"])):
        modelos, erro = listar_modelos(config)

    assert erro is None
    assert modelos == [{"id": "llama-3.1-8b", "visao": None}]


def test_listar_modelos_url_invalida_erro_seguro():
    """URL insegura → erro de validação, sem traceback."""
    modelos, erro = listar_modelos(ConfigLLM(url="file:///etc/passwd"))
    assert modelos is None
    assert "http ou https" in erro


def test_testar_ok_mede_latencia():
    """testar() retorna ok=True com latência medida em ms."""
    with patch("urllib.request.urlopen", return_value=_mock_models_response([])):
        resultado = _testar_llm()

    assert resultado["ok"] is True
    assert isinstance(resultado["latencia_ms"], int)
    assert resultado["erro"] is None


def test_testar_falha_erro_seguro():
    """Falha de conexão → ok=False com erro seguro (CWE-209)."""
    os.environ.pop("PDF2MD_LLM_URL", None)
    with patch("urllib.request.urlopen", side_effect=OSError("Connection refused")):
        resultado = _testar_llm()

    assert resultado["ok"] is False
    assert resultado["latencia_ms"] is None
    assert resultado["erro"] == "OSError"


# ── TAREFA 4: branches sem cobertura ─────────────────────────────────────────

def _mock_resp_bytes(corpo: bytes) -> MagicMock:
    """Mock de resposta HTTP com corpo JSON arbitrário (válido ou não)."""
    resp = MagicMock()
    resp.read.return_value = corpo
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def test_erro_seguro_timeout_vira_timeout():
    """TimeoutError vira a mensagem segura 'timeout' (CWE-209)."""
    assert _erro_seguro(TimeoutError("timed out")) == "timeout"


def test_testar_timeout_erro_seguro():
    """testar() com TimeoutError → ok=False e erro 'timeout'."""
    with patch("urllib.request.urlopen", side_effect=TimeoutError("timed out")):
        resultado = _testar_llm()

    assert resultado["ok"] is False
    assert resultado["erro"] == "timeout"


def test_melhorar_markdown_conteudo_nao_str(tmp_path):
    """Content não-str (dict) → TypeError controlado, original preservado.

    _completar exige str no contrato; resposta em formato inesperado não
    pode vazar para o Markdown final.
    """
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    corpo = json.dumps({"choices": [{"message": {"content": {"nada": 1}}}]}).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=_mock_resp_bytes(corpo)):
        resultado, avisos = melhorar_markdown("texto original", f)

    assert resultado == "texto original"
    assert any("TypeError" in aviso for aviso in avisos)


def test_listar_modelos_resposta_nao_dict():
    """Resposta JSON não-dict (lista) → (None, 'resposta inesperada')."""
    with patch("urllib.request.urlopen", return_value=_mock_resp_bytes(b'["a", "b"]')):
        modelos, erro = listar_modelos()

    assert modelos is None
    assert erro == "resposta inesperada"


def test_testar_resposta_nao_dict():
    """testar() com resposta não-dict → ok=False, sem latência."""
    with patch("urllib.request.urlopen", return_value=_mock_resp_bytes(b'["a", "b"]')):
        resultado = _testar_llm()

    assert resultado["ok"] is False
    assert resultado["latencia_ms"] is None
    assert resultado["erro"] == "resposta inesperada"


def test_testar_url_invalida_erro_de_validacao():
    """testar() com URL insegura → ok=False com erro de validação (SSRF)."""
    resultado = _testar_llm(ConfigLLM(url="file:///etc/passwd"))

    assert resultado["ok"] is False
    assert "http ou https" in resultado["erro"]


def test_listar_modelos_show_falha_visao_desconhecida():
    """Falha do /api/show (Ollama) → visao None, sem derrubar a listagem."""
    from urllib.error import HTTPError

    chamadas = []

    def responder(req, timeout=None):
        chamadas.append(req.full_url)
        if req.full_url.endswith("/models"):
            return _mock_models_response(["llama3.2-vision"])
        raise HTTPError("http://localhost:11434/api/show", 500, "Internal", {}, None)

    config = ConfigLLM(url="http://localhost:11434/v1")
    with patch("urllib.request.urlopen", side_effect=responder):
        modelos, erro = listar_modelos(config)

    assert erro is None
    assert modelos == [{"id": "llama3.2-vision", "visao": None}]
    assert any("/api/show" in url for url in chamadas)


def test_listar_modelos_show_flag_visao_no_nivel_raiz():
    """/api/show com 'vision' no nível raiz (sem model_info) → True."""
    chamadas = []

    def responder(req, timeout=None):
        chamadas.append(req.full_url)
        if req.full_url.endswith("/models"):
            return _mock_models_response(["llama3.2-vision"])
        return _mock_resp_bytes(json.dumps({"vision": True}).encode("utf-8"))

    config = ConfigLLM(url="http://localhost:11434/v1")
    with patch("urllib.request.urlopen", side_effect=responder):
        modelos, _ = listar_modelos(config)

    assert modelos == [{"id": "llama3.2-vision", "visao": True}]


def test_listar_modelos_show_sem_flag_visao():
    """/api/show sem flag vision → visao None (desconhecido)."""
    def responder(req, timeout=None):
        if req.full_url.endswith("/models"):
            return _mock_models_response(["llama3.2-vision"])
        return _mock_resp_bytes(json.dumps({"model_info": {"foo": 1}}).encode("utf-8"))

    config = ConfigLLM(url="http://localhost:11434/v1")
    with patch("urllib.request.urlopen", side_effect=responder):
        modelos, _ = listar_modelos(config)

    assert modelos == [{"id": "llama3.2-vision", "visao": None}]


def test_listar_modelos_json_malformado():
    """JSON inválido do endpoint → (None, 'JSONDecodeError'), sem crash."""
    with patch("urllib.request.urlopen", return_value=_mock_resp_bytes(b"isto nao e json")):
        modelos, erro = listar_modelos()

    assert modelos is None
    assert erro == "JSONDecodeError"


def test_testar_json_malformado():
    """JSON inválido do endpoint → ok=False com erro seguro."""
    with patch("urllib.request.urlopen", return_value=_mock_resp_bytes(b"isto nao e json")):
        resultado = _testar_llm()

    assert resultado["ok"] is False
    assert resultado["erro"] == "JSONDecodeError"


def test_melhorar_markdown_resposta_truncada_preserva_original(tmp_path):
    """finish_reason "length" → resposta cortada em max_tokens.

    Aceitar o texto parcial apagaria o fim do documento em silêncio; o
    original é mantido e o aviso nomeia a causa.
    """
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"x")
    corpo = json.dumps({
        "choices": [{"message": {"content": "texto cort"}, "finish_reason": "length"}]
    }).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=_mock_resp_bytes(corpo)):
        resultado, avisos = melhorar_markdown("texto original completo", f)

    assert resultado == "texto original completo"
    assert any("RespostaTruncadaError" in aviso for aviso in avisos)


def test_ocr_com_visao_truncado_nao_usa_texto(tmp_path):
    """finish_reason "length" no OCR com visão → texto vazio + aviso, nunca o parcial.

    Mesmo contrato de melhorar_markdown: a resposta cortada não entra no Markdown.
    """
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"x" * 100)
    corpo = json.dumps({
        "choices": [{"message": {"content": "texto cort"}, "finish_reason": "length"}]
    }).encode("utf-8")

    with patch("urllib.request.urlopen", return_value=_mock_resp_bytes(corpo)):
        resultado, avisos = ocr_com_visao(img)

    assert resultado == ""
    assert "texto cort" not in resultado
    assert any("RespostaTruncadaError" in aviso for aviso in avisos)
