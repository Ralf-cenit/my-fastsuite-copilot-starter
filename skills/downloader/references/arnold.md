# Arnold translators reference

Use this reference for `OLPTranslators/Arnold/` (downloaders `Arnold2D`, `Arnold2DS`, `Arnold5X`; uploader `Arnold5X_ul`).

## Authority and evidence

The detailed per-translator rules live in `.github/instructions/` (auto-attached by GitHub Copilot via `applyTo`; Codex must read the matching file explicitly):

| Translator | Rules |
|------------|-------|
| `OLPTranslators/Arnold/Arnold2D.py` | `.github/instructions/Arnold2D.instructions.md` |
| `OLPTranslators/Arnold/Arnold2DS.py` | `.github/instructions/Arnold2DS.instructions.md` |
| `OLPTranslators/Arnold/Arnold5X.py` | `.github/instructions/Arnold5X.instructions.md` |
| `OLPTranslators/Arnold/Arnold5X_ul.py` | `.github/instructions/Arnold5X_ul.instructions.md` |

- Uploader parsing notes: `docs/ctrl-specific/Arnold/spec/Arnold5X_Uploader.txt`
- Uploader sample program: `OLPTranslators/Arnold/tests/fixtures/PRG001.iso`
- Uploader tests: `OLPTranslators/Arnold/tests/test_Arnold5X_ul.py`

## Required invariants

- Each translator is standalone: no imports between Arnold translators, downloader, or other vendors.
- Write lines via `FileUtility.AppendTextToFile()` without a manual line terminator; the utility adds it.
- Header/footer text is fixed output; do not reformat it. Block numbering (`N<number>`) applies only to nonblank lines.
- Positions are in metres in E2 and millimetres in the ISO output.
- Reset per-file parser state in the uploader's `ParseFile()`.
