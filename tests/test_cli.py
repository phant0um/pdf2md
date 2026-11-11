"""
Testes para core/cli.py — comando converter via CliRunner.
"""

import json
import re
from unittest.mock import patch

from typer.testing import CliRunner

from core.cli import app
from core.utils import ModoImagem

runner = CliRunner()

_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _sem_ansi(texto: str) -> str:
    """Remove códigos ANSI do output do Rich para asserções de substring."""
    return _ANSI_RE.sub("", texto)


def _lote_simulado(resultados):
    """Substituto de batch_convert que reproduz os callbacks como o batch real.

    No --json as linhas vão pelo fd original (não por sys.stdout), então o
    teste lê com capfd; aqui basta disparar ao_concluir por resultado.
    """
    def _fake(**kwargs):
        for res in resultados:
            if kwargs.get("ao_concluir") is not None:
                kwargs["ao_concluir"](res)
        return resultados
    return _fake


def test_cli_help():
    """--help mostra usage e opções."""
    result = runner.invoke(app, ["converter", "--help"])
    assert result.exit_code == 0
    assert "pdf2md" in result.stdout.lower() or "convert" in result.stdout.lower()


def test_cli_converter_ajuda():
    """converter --help mostra opções do comando."""
    result = runner.invoke(app, ["converter", "--help"])
    assert result.exit_code == 0
    assert "--ignorar-margens" in _sem_ansi(result.stdout)


def test_cli_path_traversal_rejeitado(tmp_path):
    """Path traversal no argumento de origem é rejeitado."""
    result = runner.invoke(app, ["converter", "../../etc/passwd", str(tmp_path)])
    assert result.exit_code != 0


def test_cli_origem_inexistente(tmp_path):
    """Origem não existente retorna erro."""
    result = runner.invoke(app, ["converter", str(tmp_path / "nao_existe.pdf"), str(tmp_path)])
    assert result.exit_code != 0


# ── _requer_ocr ──────────────────────────────────────────────────────────────

def test_requer_ocr_arquivo_pdf(tmp_path):
    """PDF requer OCR (potencialmente escaneado)."""
    from core.cli import _requer_ocr
    arq = tmp_path / "documento.pdf"
    arq.write_bytes(b"%PDF dummy")
    assert _requer_ocr(arq) is True


def test_requer_ocr_arquivo_docx(tmp_path):
    """DOCX não requer OCR."""
    from core.cli import _requer_ocr
    arq = tmp_path / "documento.docx"
    arq.write_text("dummy")
    assert _requer_ocr(arq) is False


def test_requer_ocr_diretorio_com_pdf(tmp_path):
    """Diretório com PDF requer OCR."""
    from core.cli import _requer_ocr
    (tmp_path / "arquivo.pdf").write_bytes(b"%PDF dummy")
    assert _requer_ocr(tmp_path) is True


def test_requer_ocr_diretorio_somente_docx(tmp_path):
    """Diretório só com DOCX não requer OCR."""
    from core.cli import _requer_ocr
    (tmp_path / "arquivo.docx").write_text("dummy")
    assert _requer_ocr(tmp_path) is False


def test_requer_ocr_docx_com_ambos(tmp_path):
    """DOCX com --imagens ambos exige OCR; com transcrever/extrair não."""
    from core.cli import _requer_ocr
    arq = tmp_path / "documento.docx"
    arq.write_text("dummy")
    assert _requer_ocr(arq, ModoImagem.ambos) is True
    assert _requer_ocr(arq, ModoImagem.transcrever) is False
    assert _requer_ocr(arq, ModoImagem.extrair) is False


def test_requer_ocr_diretorio_e_lista_docx_com_ambos(tmp_path):
    """Diretório e lista (stdin) só com DOCX exigem OCR em modo ambos."""
    from core.cli import _requer_ocr
    (tmp_path / "arquivo.docx").write_text("dummy")
    assert _requer_ocr(tmp_path, ModoImagem.ambos) is True
    assert _requer_ocr([tmp_path / "arquivo.docx"], ModoImagem.ambos) is True


def test_cli_docx_ambos_sem_tesseract_bloqueia(tmp_path):
    """docx + --imagens ambos sem Tesseract: erro de Tesseract, exit 1."""
    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")

    with patch("core.image_converter.verificar_tesseract", return_value=False):
        result = runner.invoke(app, [
            "converter", str(origem), str(tmp_path), "--imagens", "ambos",
        ])

    assert result.exit_code == 1
    assert "Tesseract" in _sem_ansi(result.output)


def test_cli_docx_sem_ambos_nao_bloqueia_sem_tesseract(tmp_path):
    """docx sem --imagens ambos continua sem exigir Tesseract."""
    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")

    with (
        patch("core.image_converter.verificar_tesseract", return_value=False),
        patch("core.batch.batch_convert", return_value=[]),
        patch("core.cli.Progress"),
    ):
        result = runner.invoke(app, ["converter", str(origem), str(tmp_path)])

    assert "Tesseract" not in _sem_ansi(result.output)


# ── Flags LLM (T8): propagação flag > env > default ─────────────────────────

def test_cli_converter_flags_llm_propagadas(tmp_path):
    """--llm-url e --llm-modelo chegam ao batch_convert."""
    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")  # docx → sem checagem de Tesseract

    with (
        patch("core.batch.batch_convert", return_value=[]) as batch_mock,
        patch("core.cli.Progress"),
    ):
        result = runner.invoke(app, [
            "converter", str(origem), str(tmp_path),
            "--llm-url", "http://localhost:11434/v1",
            "--llm-modelo", "llama3.2-vision",
        ])

    assert result.exit_code == 0
    chamada = batch_mock.call_args
    config = chamada.kwargs["llm_config"]
    assert config.url == "http://localhost:11434/v1"
    assert config.modelo == "llama3.2-vision"


def test_cli_converter_sem_flags_llm_passa_none(tmp_path):
    """Sem flags → batch_convert recebe None (env-only preservado)."""
    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")

    with (
        patch("core.batch.batch_convert", return_value=[]) as batch_mock,
        patch("core.cli.Progress"),
    ):
        result = runner.invoke(app, ["converter", str(origem), str(tmp_path)])

    assert result.exit_code == 0
    assert batch_mock.call_args.kwargs["llm_config"] is None


def test_cli_converter_ajuda_mostra_flags_llm():
    """--help do converter lista as flags LLM."""
    result = runner.invoke(app, ["converter", "--help"])
    assert result.exit_code == 0
    assert "--llm-url" in _sem_ansi(result.stdout)
    assert "--llm-modelo" in _sem_ansi(result.stdout)


# ── Flags --imagens / --assets-dir (T4) ──────────────────────────────────────

def test_cli_converter_flags_imagens_propagadas(tmp_path):
    """--imagens e --assets-dir chegam ao batch_convert."""
    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")  # docx → sem checagem de Tesseract

    with (
        patch("core.batch.batch_convert", return_value=[]) as batch_mock,
        patch("core.cli.Progress"),
    ):
        result = runner.invoke(app, [
            "converter", str(origem), str(tmp_path),
            "--imagens", "extrair",
            "--assets-dir", str(tmp_path / "assets"),
        ])

    assert result.exit_code == 0
    chamada = batch_mock.call_args
    assert chamada.kwargs["modo_imagem"].value == "extrair"
    assert chamada.kwargs["assets_dir"] == tmp_path / "assets"


def test_cli_converter_imagens_padrao_transcrever(tmp_path):
    """Sem --imagens → ModoImagem.transcrever (backward-compatible)."""
    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")

    with (
        patch("core.batch.batch_convert", return_value=[]) as batch_mock,
        patch("core.cli.Progress"),
    ):
        result = runner.invoke(app, ["converter", str(origem), str(tmp_path)])

    assert result.exit_code == 0
    assert batch_mock.call_args.kwargs["modo_imagem"] == ModoImagem.transcrever


def test_cli_converter_ajuda_mostra_imagens():
    """--help do converter lista --imagens e --assets-dir."""
    result = runner.invoke(app, ["converter", "--help"])
    assert result.exit_code == 0
    assert "--imagens" in _sem_ansi(result.stdout)
    assert "--assets-dir" in _sem_ansi(result.stdout)


# ── Subcomandos llm (T9) ─────────────────────────────────────────────────────

def test_cli_llm_help():
    """llm --help lista modelos e testar."""
    result = runner.invoke(app, ["llm", "--help"])
    assert result.exit_code == 0
    assert "modelos" in _sem_ansi(result.stdout)
    assert "testar" in _sem_ansi(result.stdout)


def test_cli_llm_modelos_json_ok():
    """llm modelos --json emite lista de modelos no stdout."""
    modelos = [{"id": "llama3.2-vision", "visao": True}, {"id": "llama3.1", "visao": None}]
    with patch("core.llm_enhancer.listar_modelos", return_value=(modelos, None)):
        result = runner.invoke(app, ["llm", "modelos", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert [m["id"] for m in payload["modelos"]] == ["llama3.2-vision", "llama3.1"]


def test_cli_llm_modelos_json_falha_sem_traceback():
    """Falha → exit 0 com {"ok": false} — o JSON é o contrato com a GUI."""
    with patch("core.llm_enhancer.listar_modelos", return_value=(None, "servidor inacessível")):
        result = runner.invoke(app, ["llm", "modelos", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["modelos"] == []
    assert "Traceback" not in result.output


def test_cli_llm_testar_json_ok():
    """llm testar --json emite ok + latência."""
    with patch("core.llm_enhancer.testar", return_value={"ok": True, "latencia_ms": 42, "erro": None}):
        result = runner.invoke(app, ["llm", "testar", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["latencia_ms"] == 42


def test_cli_llm_testar_json_falha_sem_traceback():
    """Falha → exit 0 com {"ok": false}, sem traceback no output."""
    with patch("core.llm_enhancer.testar", return_value={"ok": False, "latencia_ms": None, "erro": "HTTP 401"}):
        result = runner.invoke(app, ["llm", "testar", "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["ok"] is False
    assert payload["erro"] == "HTTP 401"
    assert "Traceback" not in result.output


# ── Default-command (C1): pdf2md <arquivo> <destino> dispensa "converter" ────

def test_cli_main_shim_injeta_converter(monkeypatch):
    """Entry point real injeta `converter` quando o 1º arg não é comando."""
    from core.cli import main

    chamadas = []
    with monkeypatch.context() as m:
        m.setattr("sys.argv", ["pdf2md", "arquivo.pdf", "saida/"])
        m.setattr("core.cli.app", type("FakeApp", (), {"__call__": lambda self: chamadas.append("app")})())
        main()
        # sys.argv foi mutado ANTES de chamar app — o shim funcionou
        assert chamadas == ["app"]


def test_cli_main_shim_nao_injeta_para_comando(monkeypatch):
    """`pdf2md llm modelos` NÃO ganha prefixo converter."""
    from core.cli import main

    chamadas = []
    with monkeypatch.context() as m:
        m.setattr("sys.argv", ["pdf2md", "llm", "modelos", "--json"])
        m.setattr("core.cli.app", type("FakeApp", (), {"__call__": lambda self: chamadas.append("app")})())
        main()
        assert chamadas == ["app"]
        import sys as _sys
        assert _sys.argv[1] == "llm"  # sem prefixo"


def test_cli_main_shim_nao_injeta_para_flag(monkeypatch):
    """`pdf2md --help` não vira `converter --help`."""
    from core.cli import main

    chamadas = []
    with monkeypatch.context() as m:
        m.setattr("sys.argv", ["pdf2md", "--help"])
        m.setattr("core.cli.app", type("FakeApp", (), {"__call__": lambda self: chamadas.append("app")})())
        main()
        assert chamadas == ["app"]
        import sys as _sys
        assert _sys.argv[1] == "--help"  # sem prefixo"


# ── TAREFA 1: contrato JSON / exit-codes reais do CLI ────────────────────────

def test_cli_converter_json_linhas_de_status(tmp_path, capfd):
    """converter --json emite 1 linha JSON por resultado (status/erro/avisos)."""
    from core.batch import ResultadoArquivo, StatusArquivo

    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")

    resultados = [
        ResultadoArquivo(
            origem=tmp_path / "a.pdf", destino=tmp_path / "a.md",
            status=StatusArquivo.CONCLUIDO, erro=None, avisos=[],
        ),
        ResultadoArquivo(
            origem=tmp_path / "b.pdf", destino=tmp_path / "b.md",
            status=StatusArquivo.CONCLUIDO, erro=None,
            avisos=["palavra quebrada por hifenização"],
        ),
        ResultadoArquivo(
            origem=tmp_path / "c.pdf", destino=None,
            status=StatusArquivo.ERRO, erro="falha no processamento", avisos=[],
        ),
        ResultadoArquivo(
            origem=tmp_path / "d.pdf", destino=None,
            status=StatusArquivo.IGNORADO, erro=None, avisos=[],
        ),
    ]

    with (
        patch("core.batch.batch_convert", side_effect=_lote_simulado(resultados)),
        patch("core.cli.Progress"),
    ):
        result = runner.invoke(app, ["converter", str(origem), str(tmp_path), "--json"])

    assert result.exit_code == 0
    linhas = [json.loads(linha) for linha in capfd.readouterr().out.splitlines() if linha.strip().startswith("{")]
    assert len(linhas) == 4
    por_status: dict[str, list[dict]] = {}
    for linha in linhas:
        por_status.setdefault(linha["status"], []).append(linha)

    ids_concluidos = {item["id"] for item in por_status["concluido"]}
    assert ids_concluidos == {str(tmp_path / "a.pdf"), str(tmp_path / "b.pdf")}
    assert all(item["erro"] is None for item in por_status["concluido"])
    assert all(item["avisos"] == [] for item in por_status["concluido"][:1])
    # mesmo status com aviso → campo avisos preenchido
    assert "palavra quebrada por hifenização" in por_status["concluido"][1]["avisos"]
    assert por_status["erro"][0]["erro"] == "falha no processamento"
    assert "destino" not in por_status["erro"][0]  # contrato: id/status/erro/avisos
    assert por_status["ignorado"][0]["status"] == "ignorado"


def test_cli_converter_json_sem_traceback_extra(tmp_path, capfd):
    """--json emite APENAS as linhas JSON no stdout (sem ruído/Progress)."""
    from core.batch import ResultadoArquivo, StatusArquivo

    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")
    resultados = [
        ResultadoArquivo(
            origem=tmp_path / "a.pdf", destino=tmp_path / "a.md",
            status=StatusArquivo.CONCLUIDO, erro=None, avisos=[],
        )
    ]

    with (
        patch("core.batch.batch_convert", side_effect=_lote_simulado(resultados)),
        patch("core.cli.Progress"),
    ):
        result = runner.invoke(app, ["converter", str(origem), str(tmp_path), "--json"])

    linhas = [linha for linha in capfd.readouterr().out.splitlines() if linha.strip()]
    assert len(linhas) == 1
    assert json.loads(linhas[0])["status"] == "concluido"
    assert "Traceback" not in result.output


def test_cli_converter_tabela_com_aviso_e_sumario(tmp_path):
    """Sem --json: tabela com status 'concluido⚠' + seção de avisos + sumário."""
    from core.batch import ResultadoArquivo, StatusArquivo

    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")
    resultados = [
        ResultadoArquivo(
            origem=tmp_path / "a.pdf", destino=tmp_path / "a.md",
            status=StatusArquivo.CONCLUIDO, erro=None,
            avisos=["palavra quebrada por hifenização"],
        ),
        ResultadoArquivo(
            origem=tmp_path / "b.pdf", destino=None,
            status=StatusArquivo.ERRO, erro="falha no processamento", avisos=[],
        ),
    ]

    with (
        patch("core.batch.batch_convert", return_value=resultados),
        patch("core.cli.Progress"),
    ):
        result = runner.invoke(app, ["converter", str(origem), str(tmp_path)])

    saida = _sem_ansi(result.output)
    assert result.exit_code == 0
    assert "concluido" in saida
    assert "Avisos de qualidade" in saida
    assert "palavra quebrada por hifenização" in saida
    assert "Total:" in saida and "Erros:" in saida and "OK:" in saida


def test_cli_converter_tesseract_ausente_json(tmp_path):
    """Tesseract ausente + --json → exit 1 com erro JSON limpo (contrato GUI)."""
    origem = tmp_path / "documento.pdf"  # PDF → requer OCR
    origem.write_bytes(b"%PDF-1.4 dummy")

    with patch("core.image_converter.verificar_tesseract", return_value=False):
        result = runner.invoke(app, ["converter", str(origem), str(tmp_path), "--json"])

    assert result.exit_code == 1
    linhas = [json.loads(linha) for linha in result.stdout.splitlines() if linha.strip().startswith("{")]
    assert len(linhas) == 1
    assert linhas[0]["status"] == "erro"
    assert linhas[0]["id"] == str(origem)
    assert "Tesseract não encontrado" in linhas[0]["erro"]
    assert "Traceback" not in result.output


def test_cli_converter_tesseract_ausente_sem_json(tmp_path):
    """Tesseract ausente sem --json → exit 1 + mensagem de instalação."""
    origem = tmp_path / "documento.pdf"
    origem.write_bytes(b"%PDF-1.4 dummy")

    with patch("core.image_converter.verificar_tesseract", return_value=False):
        result = runner.invoke(app, ["converter", str(origem), str(tmp_path)])

    assert result.exit_code == 1
    assert "Tesseract não encontrado" in _sem_ansi(result.output)
    assert "brew install tesseract" in _sem_ansi(result.output)


def test_cli_converter_tesseract_ok_docx_nao_bloqueia(tmp_path):
    """.docx não requer OCR → conversão segue mesmo com Tesseract ausente."""
    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")

    with (
        patch("core.image_converter.verificar_tesseract", return_value=False),
        patch("core.batch.batch_convert", return_value=[]),
        patch("core.cli.Progress"),
    ):
        result = runner.invoke(app, ["converter", str(origem), str(tmp_path)])

    assert result.exit_code == 0


def test_cli_converter_vault_inexistente(tmp_path):
    """--vault apontando para path inexistente → exit 1 + 'Vault inválido'."""
    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")

    result = runner.invoke(app, [
        "converter", str(origem), str(tmp_path),
        "--vault", str(tmp_path / "vault_nao_existe"),
    ])

    assert result.exit_code == 1
    assert "Vault inválido" in _sem_ansi(result.output)


def test_cli_converter_vault_arquivo_em_vez_de_diretorio(tmp_path):
    """--vault apontando para um ARQUIVO → exit 1 + 'Vault inválido'."""
    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")
    arquivo = tmp_path / "nao_eh_pasta"
    arquivo.write_text("sou um arquivo")

    result = runner.invoke(app, [
        "converter", str(origem), str(tmp_path), "--vault", str(arquivo),
    ])

    assert result.exit_code == 1
    assert "Vault inválido" in _sem_ansi(result.output)


def test_cli_converter_vault_traversal_rejeitado(tmp_path):
    """--vault com path traversal → exit 1 + 'Erro de validação'."""
    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")

    result = runner.invoke(app, [
        "converter", str(origem), str(tmp_path),
        "--vault", str(tmp_path / ".." / ".." / "etc"),
    ])

    assert result.exit_code == 1
    assert "Erro de validação" in _sem_ansi(result.output)


def test_cli_converter_vault_valido_forca_obsidian(tmp_path):
    """--vault válido → repassado ao batch_convert com obsidian=True."""
    origem = tmp_path / "documento.docx"
    origem.write_text("dummy")
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()

    with (
        patch("core.batch.batch_convert", return_value=[]) as batch_mock,
        patch("core.cli.Progress"),
    ):
        result = runner.invoke(app, [
            "converter", str(origem), str(tmp_path), "--vault", str(vault_dir),
        ])

    assert result.exit_code == 0
    assert batch_mock.call_args.kwargs["vault"] == vault_dir
    assert batch_mock.call_args.kwargs["obsidian"] is True


# ── llm modelos / llm testar SEM --json (contrato de exit codes) ────────────

def test_cli_llm_modelos_sem_json_falha_exit_1():
    """Falha sem --json → exit 1 + dica (o JSON não é o contrato aqui)."""
    with patch("core.llm_enhancer.listar_modelos", return_value=(None, "servidor inacessível")):
        result = runner.invoke(app, ["llm", "modelos"])

    assert result.exit_code == 1
    saida = _sem_ansi(result.output)
    assert "Falha ao listar modelos" in saida
    assert "PDF2MD_LLM_URL" in saida


def test_cli_llm_modelos_sem_json_nenhum_modelo():
    """Endpoint OK mas lista vazia → exit 0 + 'Nenhum modelo'."""
    with patch("core.llm_enhancer.listar_modelos", return_value=([], None)):
        result = runner.invoke(app, ["llm", "modelos"])

    assert result.exit_code == 0
    assert "Nenhum modelo" in _sem_ansi(result.output)


def test_cli_llm_modelos_sem_json_tabela():
    """Lista com modelos → tabela renderizada com ids e coluna Visão."""
    modelos = [{"id": "llama3.2-vision", "visao": True}, {"id": "llama3.1", "visao": None}]
    with patch("core.llm_enhancer.listar_modelos", return_value=(modelos, None)):
        result = runner.invoke(app, ["llm", "modelos"])

    assert result.exit_code == 0
    saida = _sem_ansi(result.output)
    assert "llama3.2-vision" in saida
    assert "llama3.1" in saida
    assert "Visão" in saida


def test_cli_llm_testar_sem_json_ok():
    """testar OK sem --json → exit 0 + 'Conectado' com latência."""
    with patch("core.llm_enhancer.testar", return_value={"ok": True, "latencia_ms": 42, "erro": None}):
        result = runner.invoke(app, ["llm", "testar"])

    assert result.exit_code == 0
    saida = _sem_ansi(result.output)
    assert "Conectado" in saida
    assert "42ms" in saida


def test_cli_llm_testar_sem_json_falha_exit_1():
    """testar falha sem --json → exit 1 + 'Inacessível'."""
    with patch(
        "core.llm_enhancer.testar",
        return_value={"ok": False, "latencia_ms": None, "erro": "HTTP 401"},
    ):
        result = runner.invoke(app, ["llm", "testar"])

    assert result.exit_code == 1
    saida = _sem_ansi(result.output)
    assert "Inacessível" in saida
    assert "HTTP 401" in saida


def test_cli_workers_zero_rejeitado_sem_traceback(tmp_path):
    """--workers 0 → erro de uso do Typer, não ValueError do ThreadPoolExecutor."""
    f = tmp_path / "a.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")
    result = runner.invoke(app, ["converter", str(f), str(tmp_path / "out"), "--workers", "0"])
    assert result.exit_code == 2
    assert not isinstance(result.exception, ValueError)


# ── ORIGEM `-` (lista NUL-separada no stdin, protocolo da GUI) ──────────────

def test_indice_primeiro_posicional_hifen_e_posicional():
    """`-` sozinho é ORIGEM posicional, não flag (default-command)."""
    from core.cli import _indice_primeiro_posicional

    assert _indice_primeiro_posicional(["pdf2md", "--obsidian", "-", "out"]) == 2
    assert _indice_primeiro_posicional(["pdf2md", "-", "out"]) == 1


def test_cli_main_reordena_flags_antes_do_hifen(monkeypatch):
    """`pdf2md --obsidian - out` → `converter --obsidian - out`."""
    from core.cli import main

    chamadas = []
    with monkeypatch.context() as m:
        m.setattr("sys.argv", ["pdf2md", "--obsidian", "-", "out"])
        m.setattr("core.cli.app", type("FakeApp", (), {"__call__": lambda self: chamadas.append("app")})())
        main()
        assert chamadas == ["app"]
        import sys as _sys
        assert _sys.argv == ["pdf2md", "converter", "--obsidian", "-", "out"]


def test_cli_main_hifen_primeiro_injeta_converter(monkeypatch):
    """`pdf2md - out --json` → `converter - out --json` (hífen não é flag)."""
    from core.cli import main

    chamadas = []
    with monkeypatch.context() as m:
        m.setattr("sys.argv", ["pdf2md", "-", "out", "--json"])
        m.setattr("core.cli.app", type("FakeApp", (), {"__call__": lambda self: chamadas.append("app")})())
        main()
        assert chamadas == ["app"]
        import sys as _sys
        assert _sys.argv == ["pdf2md", "converter", "-", "out", "--json"]


def test_cli_stdin_vazio_exit_1(tmp_path):
    """`-` sem nenhum caminho no stdin → exit 1 com mensagem, sem conversão."""
    result = runner.invoke(
        app, ["converter", "-", str(tmp_path / "out"), "--json"], input=b"",
    )

    assert result.exit_code == 1
    assert "Nenhum arquivo" in _sem_ansi(result.output)


def test_cli_stdin_streaming_json_uma_linha_final_por_arquivo(tmp_path, capfd):
    """`-` + --json: "processando" por arquivo e 1 linha final, ids = strings do stdin.

    Lê com capfd: as linhas vão pelo fd original, não pelo sys.stdout do CliRunner.
    """
    entrada = tmp_path / "in"
    entrada.mkdir()
    a = entrada / "a.csv"
    a.write_text("x,y\n1,2\n", encoding="utf-8")
    b = entrada / "b.csv"
    b.write_text("p,q\n3,4\n", encoding="utf-8")
    caminhos = [str(a), str(b)]
    stdin = ("\0".join(caminhos) + "\0").encode("utf-8")

    result = runner.invoke(
        app, ["converter", "-", str(tmp_path / "out"), "--json"], input=stdin,
    )

    assert result.exit_code == 0, result.output
    linhas = [json.loads(linha) for linha in capfd.readouterr().out.splitlines() if linha.strip()]
    processando = [item for item in linhas if item["status"] == "processando"]
    finais = [item for item in linhas if item["status"] != "processando"]
    assert len(processando) == 2
    assert len(finais) == 2
    assert {item["id"] for item in finais} == set(caminhos)
    assert all(item["status"] == "concluido" for item in finais)
    for caminho in caminhos:
        sequencia = [item["status"] for item in linhas if item["id"] == caminho]
        assert sequencia == ["processando", "concluido"]
    assert (tmp_path / "out" / "a.md").exists()
    assert (tmp_path / "out" / "b.md").exists()
