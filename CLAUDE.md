# CLAUDE.md — pdf2md

**Stack:** Python 3.12 + pymupdf4llm + Typer | SwiftUI (macOS 13+)
**Test cmd:** `uv run pytest tests/ -v`
**Lint cmd:** `uv run ruff check core/ tests/`
**Build app:** `./scripts/build_app.sh` (PyInstaller + swiftc + codesign + DMG — o PyInstaller cru sem `--add-data` gera binário quebrado)

## Constraints

- Zero paths hardcoded. Usar `Path.home()` ou argumento CLI.
- Comentários Python + Swift: PT-BR.
- Unit tests obrigatórios após cada nova função em `core/`.
- Sem deps pagas. `uv.lock` commitado.
- Binários (`.app`, `.dmg`) → GitHub Releases. Nunca `git commit`.
- `subprocess.run()` proibido com input de usuário sem validação prévia.
- Stack traces não expõem paths do usuário final em produção.

## Conventions

- Python: `snake_case`. Swift: `CamelCase`.
- Commits: `feat:` / `fix:` / `docs:` / `chore:` / `test:` / `security:`.
- PR obrigatório para `main`. Sem push direto.

## Security

Ver `SECURITY.md`. Agente responsável: Security Agent (`security-review` skill).

## Agents (Fullstack System)

- 🏗️ Infra & Cloud — bootstrap, CI/CD, empacotamento
- 🔧 Backend Dev — `core/` (converter, batch, CLI)
- 🎨 Frontend Dev — `gui/` (SwiftUI)
- 🧪 QA Agent — `tests/` (fixtures, golden, edge cases)
- 🔒 Security Agent — validação input, subprocess, path traversal

## Task Management

`tasks/todo.md` — itens checkáveis por fase
`tasks/lessons.md` — max 30 entradas, consolidar antes de adicionar
