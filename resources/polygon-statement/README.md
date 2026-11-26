# Polygon statement files

The resource files Polygon adds to every new problem for building the statement PDF:

- `olymp.sty` — the LaTeX macros of the statement
  ([GassaFM/olymp.sty](https://github.com/GassaFM/olymp.sty), the license and the authors
  are linked in its header);
- `problem.tex` — the FreeMarker template of one problem's statement;
- `statements.ftl` — the FreeMarker template of the whole document.

- Version: as Polygon gave them to a new problem in 2026-11; the upstream commit of
  `olymp.sty` isn't known.
- The files are copied from Polygon without changes, line breaks included.

They are the defaults `polygon pull` compares a problem's statement files on Polygon with
(`orchestrator/resources.py`): the same — nothing is written; different — Polygon's version
goes to the problem's `resources/`. `polygon push` uploads only a problem's own versions.

## How to update

Create a problem on Polygon, download these three files from its "Resource files" and put
them here as is. Then run `pytest tests/test_resources.py tests/test_polygon_pull.py`.
