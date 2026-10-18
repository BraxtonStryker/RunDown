# RunDown

A Markdown word processor with live preview. You type Markdown and each piece renders **the moment it is closed**: `**bold` stays plain until the closing `**`, then it turns bold and the asterisks disappear. Put the cursor back inside and it opens up into plain Markdown again.

**Use it:** https://braxtonstryker.github.io/RunDown/

- **Live, Source and Read views.** Lines (headings, lists, quotes) render when you leave the line; code blocks, `$$` math and tables render once they're closed and the cursor is outside them.
- **Everything Markdown has, plus a bit:** headings, bold, italic, strikethrough, `==highlight==`, inline code, links, pictures (paste or drop them in), bullet, numbered and checklist items, nested quotes, rules, fenced code, GFM tables, and LaTeX math with KaTeX.
- **Word-processor comforts:** a formatting toolbar, keyboard shortcuts, lists that continue themselves, find and replace, an outline, word count, undo and redo.
- **Documents** saved in the browser as you type, import of `.md` files, and export to Markdown, a standalone web page, PDF (print), or formatted text for Google Docs and Word.
- Installable, works offline, and keeps everything in your browser. Nothing is uploaded.

## Repository

- `index.html`: the whole app, one file with inline CSS and JS, no build step and nothing loaded from other sites.
- `vendor/katex.min.js`: KaTeX (MIT, see `vendor/LICENSE-katex.txt`), loaded the first time a document has math.
- `sw.js`, `manifest.webmanifest`, `icons/`: the installable offline app. After changing any precached file, run `python -I tools/sw_version.py --sync` so browsers pick up the new version.
- `tools/harness.py`: drives the editor in headless Chrome with real key presses and checks the live preview. Run `python -I tools/harness.py`.
- See [CLAUDE.md](CLAUDE.md) for development notes.
