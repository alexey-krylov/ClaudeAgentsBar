# web/ — landing page

Single-file static landing for ClaudeAgentsBar, served via GitHub Pages.

- `index.html` — the whole site. Tailwind via Play CDN (no build step),
  dark theme, and a live JS recreation of the menu-bar dropdown in the
  hero (no screenshots required for it to look alive).
- `.nojekyll` — tells Pages to serve the files as-is.
- `assets/` — real screenshots. The page references, in order of preference:
  - `assets/shot-dropdown.jpg` — the menu bar + dropdown.
  - `assets/shot-compact.png` — compact mode (ANSI bullets).
  - `assets/og-card.png` — 1200×630 social preview (optional).

  Each `<img>` falls back to the screenshot hosted on the Substack post
  CDN, then to a labelled placeholder — so the page looks right even
  before you save local copies. For a self-contained site (no external
  dependency on substackcdn), download them into `assets/` with the
  filenames above. Source images live in the launch post:
  <https://alexeykrylov.substack.com/p/claude-agents-bar-every-claude-code>

## Local preview

```bash
python3 -m http.server -d web 8080   # then open http://localhost:8080
```

## Deploy

`.github/workflows/pages.yml` publishes this folder on every push to
`main` that touches `web/`. One-time setup: GitHub → repo **Settings →
Pages → Source: GitHub Actions**. The site lands at
`https://alexey-krylov.github.io/ClaudeAgentsBar/`.

## Updating content

The copy mirrors the root `README.md` sections (hero → counters →
features → install → how it works). When you change a feature, brew
command, or config default in the README, update the matching block
here in the same turn.
