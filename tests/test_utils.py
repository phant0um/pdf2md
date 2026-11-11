"""
Testes para core/utils.py — foco na função sanitizar_mensagem_erro
(redação de paths absolutos em mensagens de erro, CWE-209).
"""
from pathlib import Path

from core.utils import _linha_tabela_md, sanitizar_mensagem_erro


def test_linha_tabela_md_monta_linha_com_pipes():
    assert _linha_tabela_md(["a", "b"]) == "| a | b |"


def test_linha_tabela_md_separador_de_header():
    assert _linha_tabela_md(["---"] * 3) == "| --- | --- | --- |"


def test_linha_tabela_md_celula_vazia():
    assert _linha_tabela_md(["", "x"]) == "|  | x |"


def test_linha_tabela_md_celula_ja_sanitizada_nao_reescapa():
    # _linha_tabela_md não sanitiza — quem sanitiza é _sanitizar_celula_md.
    assert _linha_tabela_md(["a|b"]) == "| a|b |"


def test_sanitiza_path_simples():
    """Path absoluto simples é reduzido ao basename."""
    msg = "erro ao processar /Users/alice/Documents/segredo.pdf"
    assert sanitizar_mensagem_erro(msg) == "erro ao processar segredo.pdf"


def test_sanitiza_path_resolvido_symlink_macos():
    """
    Path resolvido via symlink (ex: /tmp → /private/var/folders/... no
    macOS) é diferente do path original passado pelo usuário — o antigo
    str.replace(str(origem), ...) falhava aqui porque as strings não
    eram idênticas. A regex captura qualquer path absoluto, então cobre
    este caso independente da origem do path na mensagem.
    """
    msg = (
        "[Errno 2] No such file or directory: "
        "'/private/var/folders/xx/T/tmpdir/segredo.pdf'"
    )
    resultado = sanitizar_mensagem_erro(msg)
    assert "segredo.pdf" in resultado
    assert "/private/var" not in resultado
    assert "/folders/" not in resultado


def test_sanitiza_case_mismatch():
    """
    Filesystems case-insensitive (HFS+/APFS padrão) podem devolver o path
    com capitalização diferente da original — a regex não depende de
    comparação exata, então cobre esse caso também.
    """
    msg = "falha em /Users/Alice/DOCUMENTS/Segredo.PDF"
    resultado = sanitizar_mensagem_erro(msg)
    assert resultado == "falha em Segredo.PDF"


def test_sanitiza_multiplos_paths_na_mesma_mensagem():
    """Mais de um path absoluto na mesma mensagem — ambos são redigidos."""
    msg = "copiando /Users/alice/origem.pdf para /Users/alice/saida/destino.md"
    resultado = sanitizar_mensagem_erro(msg)
    assert resultado == "copiando origem.pdf para destino.md"
    assert "/Users/alice" not in resultado


def test_sanitiza_path_dentro_do_home_preserva_relativo():
    """
    Path dentro do home do usuário é reduzido a "~/..." preservando a
    estrutura relativa, em vez de virar apenas o basename — útil para
    debug sem expor o path absoluto completo do sistema.
    """
    home = str(Path.home())
    msg = f"erro em {home}/projeto/sub/arquivo.pdf"
    resultado = sanitizar_mensagem_erro(msg)
    assert resultado == "erro em ~/projeto/sub/arquivo.pdf"
    assert home not in resultado


def test_sanitiza_path_home_com_trailing_slash():
    """Variante com barra final do diretório não quebra a substituição."""
    home = str(Path.home())
    msg = f"diretório inválido: {home}/"
    resultado = sanitizar_mensagem_erro(msg)
    assert home not in resultado


def test_sanitiza_mensagem_sem_path_fica_inalterada():
    """Mensagens sem path absoluto não são modificadas."""
    msg = "tipo de arquivo não suportado"
    assert sanitizar_mensagem_erro(msg) == msg


def test_sanitiza_path_com_espaco_no_username_nao_vaza_segmento():
    """
    Regressão: username default do macOS é "First Last" (com espaço). A
    regex antiga ([^\\s:]+) parava no primeiro espaço e vazava o segmento
    inteiro até ali (ex: "John Doe" aparecia cru na saída). A nova regex
    permite espaço dentro do segmento e usa PurePosixPath para extrair só
    o basename real.
    """
    msg = "[Errno 13] Permission denied: '/Users/John Doe/Desktop/confidential.pdf'"
    resultado = sanitizar_mensagem_erro(msg)
    assert "John Doe" not in resultado
    assert "confidential.pdf" in resultado


def test_sanitiza_path_relativo_com_espaco_no_segmento():
    """
    Regressão: "/nonexistent dir/file.pdf" não era redigido pela regex
    antiga (o espaço cortava a captura antes do '/' final exigido).
    """
    msg = "erro: /nonexistent dir/file.pdf não encontrado"
    resultado = sanitizar_mensagem_erro(msg)
    assert "nonexistent dir" not in resultado
    assert "file.pdf" in resultado


def test_sanitiza_path_single_segmento():
    """
    Regressão: a regex antiga exigia (?:/[^\\s:]+)+/ — ou seja, ao menos
    dois segmentos. Um path de único segmento como "/mountpoint" nunca
    casava e passava direto sem redação.
    """
    msg = "falha ao acessar /mountpoint"
    resultado = sanitizar_mensagem_erro(msg)
    assert resultado == "falha ao acessar mountpoint"


def test_sanitiza_path_home_com_espaco_no_segmento_subsequente():
    """
    Caso real combinado: path dentro do home (reduzido a "~") cujo
    restante contém espaço (ex: iCloud "Mobile Documents"). Deve
    preservar a notação "~/..." completa, sem vazar nenhum segmento
    do home original.
    """
    home = str(Path.home())
    msg = (
        f"erro em {home}/Library/Mobile Documents/com~apple~CloudDocs/"
        "relatorio.pdf"
    )
    resultado = sanitizar_mensagem_erro(msg)
    assert home not in resultado
    assert resultado == (
        "erro em ~/Library/Mobile Documents/com~apple~CloudDocs/relatorio.pdf"
    )


def _home_falso(monkeypatch, caminho: str) -> None:
    """Simula Path.home() devolvendo `caminho`; restaurado pelo monkeypatch."""
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: Path(caminho)))
    monkeypatch.setattr("core.utils._usuarios_com_espaco", lambda: [])


def test_home_curto_nao_troca_prefixo_de_outro_usuario(monkeypatch):
    """Home "/Users/al" não pode virar "~ice" em "/Users/alice" (fim de segmento)."""
    _home_falso(monkeypatch, "/Users/al")
    resultado = sanitizar_mensagem_erro("erro em /Users/alice/doc/x.pdf")
    assert resultado == "erro em x.pdf"
    assert "~" not in resultado
    assert "ice" not in resultado


def test_home_curto_troca_home_real_por_til(monkeypatch):
    """Home "/Users/al" seguido de barra é reduzido a "~/..."."""
    _home_falso(monkeypatch, "/Users/al")
    assert sanitizar_mensagem_erro("erro em /Users/al/doc/x.pdf") == "erro em ~/doc/x.pdf"


def test_home_curto_no_fim_da_mensagem_vira_til(monkeypatch):
    """Home sem nada depois (fim de string) também é trocado por "~"."""
    _home_falso(monkeypatch, "/Users/al")
    assert sanitizar_mensagem_erro("sem acesso: /Users/al") == "sem acesso: ~"


def test_home_curto_antes_de_dois_pontos_vira_til(monkeypatch):
    """Dois-pontos fecha o segmento: home vira "~" também antes de ":"."""
    _home_falso(monkeypatch, "/Users/al")
    assert sanitizar_mensagem_erro("erro em /Users/al: negado") == "erro em ~: negado"


def test_home_raiz_nao_troca_barras_por_til(monkeypatch):
    """Home "/" (daemons, sandboxes) é ignorado: não pode virar "~" em toda barra."""
    _home_falso(monkeypatch, "/")
    assert sanitizar_mensagem_erro("erro em /etc/passwd") == "erro em passwd"


def test_sanitiza_path_username_com_espaco_dentro_do_home_preservado():
    """
    Cenário do PoC do bloqueador #3, mas com o "home" simulado contendo
    espaço — garante que o prefixo "~" e o restante do path (com espaço)
    seguem preservados juntos, sem vazar apenas o fragmento do username.
    """
    msg = "erro em /Users/John Doe/Desktop/x.pdf"
    resultado = sanitizar_mensagem_erro(msg)
    assert "John Doe" not in resultado
    assert resultado == "erro em x.pdf"


def test_sanitiza_preserva_texto_ao_redor_do_path():
    """Texto antes/depois do path é preservado intacto."""
    msg = "ValueError: Path fora do diretório permitido: /etc/passwd (verifique permissões)"
    resultado = sanitizar_mensagem_erro(msg)
    assert resultado == (
        "ValueError: Path fora do diretório permitido: passwd (verifique permissões)"
    )


def test_sanitiza_path_outro_usuario_nao_vaza_username():
    """
    Finding #5 (CWE-209): "/Users/bob" em volume compartilhado revelaria
    o username de terceiro. Deve ser redigido para "[user]".
    """
    msg = "acesso negado: /Users/bob"
    resultado = sanitizar_mensagem_erro(msg)
    assert "bob" not in resultado
    assert resultado == "acesso negado: [user]"


def test_sanitiza_path_outro_usuario_com_espaco_nao_vaza_username(monkeypatch):
    """
    Finding #5 com username contendo espaço: "/Users/John Doe" → "[user]".
    Conta local com espaço é redigida inteira antes da regex de path.
    """
    monkeypatch.setattr("core.utils._usuarios_com_espaco", lambda: ["John Doe"])
    msg = "erro: /Users/John Doe"
    resultado = sanitizar_mensagem_erro(msg)
    assert "John" not in resultado
    assert "Doe" not in resultado
    assert resultado == "erro: [user]"


def test_sanitiza_usuario_sem_conta_local_preserva_palavra_seguinte(monkeypatch):
    """
    Regressão: a segunda passada antiga consumia a palavra após "[user]",
    gerando "[user] encontrado" para "/Users/bob não encontrado".
    """
    monkeypatch.setattr("core.utils._usuarios_com_espaco", lambda: [])
    assert sanitizar_mensagem_erro("/Users/bob não encontrado") == "[user] não encontrado"


def test_sanitiza_conta_local_com_espaco_seguida_de_texto(monkeypatch):
    """Conta local com espaço seguida de texto: só o username some."""
    monkeypatch.setattr("core.utils._usuarios_com_espaco", lambda: ["John Doe"])
    assert sanitizar_mensagem_erro("/Users/John Doe não existe") == "[user] não existe"


def test_sanitiza_conta_local_com_espaco_em_path_profundo(monkeypatch):
    """Conta local com espaço em path profundo: reduz ao basename."""
    monkeypatch.setattr("core.utils._usuarios_com_espaco", lambda: ["John Doe"])
    assert sanitizar_mensagem_erro("/Users/John Doe/Desktop/x.pdf") == "x.pdf"


def test_sanitiza_prefixo_de_conta_local_nao_casa_no_meio_de_palavra(monkeypatch):
    """Limite: "John Doe" não casa dentro de "John Doery" (pré-passe não dispara)."""
    monkeypatch.setattr("core.utils._usuarios_com_espaco", lambda: ["John Doe"])
    assert sanitizar_mensagem_erro("/Users/John Doery") == "[user] Doery"


def test_usuarios_com_espaco_lista_do_maior_para_o_menor(tmp_path, monkeypatch):
    """Lista só diretórios com espaço, do nome mais longo para o mais curto."""
    from core import utils

    for nome in ("Ana Lima", "bob", "Ana Lima Souza"):
        (tmp_path / nome).mkdir()
    monkeypatch.setattr(utils, "_DIR_USUARIOS", tmp_path)
    assert utils._usuarios_com_espaco() == ["Ana Lima Souza", "Ana Lima"]


def test_usuarios_com_espaco_diretorio_ausente_devolve_vazio(tmp_path, monkeypatch):
    """Diretório inexistente não levanta: devolve lista vazia."""
    from core import utils

    monkeypatch.setattr(utils, "_DIR_USUARIOS", tmp_path / "nao-existe")
    assert utils._usuarios_com_espaco() == []


def test_sanitizar_celula_md_quebras_cr_e_crlf_viram_espaco():
    """CR e CRLF dentro da célula também partiriam a linha da tabela."""
    from core.utils import _sanitizar_celula_md

    assert _sanitizar_celula_md("a\r\nb\rc\nd") == "a b c d"
