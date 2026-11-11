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


def test_sanitiza_path_outro_usuario_com_espaco_nao_vaza_username():
    """
    Finding #5 com username contendo espaço: "/Users/John Doe" → "[user]".
    A regex casa só "/Users/John" (para no espaço), mas a segunda passada
    consome " Doe" que sobraria.
    """
    msg = "erro: /Users/John Doe"
    resultado = sanitizar_mensagem_erro(msg)
    assert "John" not in resultado
    assert "Doe" not in resultado
    assert resultado == "erro: [user]"


def test_sanitizar_celula_md_quebras_cr_e_crlf_viram_espaco():
    """CR e CRLF dentro da célula também partiriam a linha da tabela."""
    from core.utils import _sanitizar_celula_md

    assert _sanitizar_celula_md("a\r\nb\rc\nd") == "a b c d"
