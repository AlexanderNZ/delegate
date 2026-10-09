# Handoff: delegate docs theme ("2c — Guide + quiet reference")

## Overview
This is a new visual theme for the `delegate` docs site. The site is built with Zensical from `zensical.toml` and published to GitHub Pages. The theme is a tribute to classic open-source documentation. It takes cues from Asciidoctor and Spring reference guides: serif body text, light sans headings, numbered sections and a numbered table of contents. Reference pages also borrow a man-page structure: a header line, NAME and SYNOPSIS, and labelled, striped tables.

Repo: `AlexanderNZ/delegate`, branch `main`. The site currently runs stock Zensical with no custom CSS or overrides.

## About the design files
The files in `design/` are **design references built in HTML**. They are prototypes that show the intended look and behaviour. They are not production code to copy. The task is to **recreate this design in the repo's Zensical setup** with the documented extension points:

- `extra_css` in `[project]`, for example `docs/stylesheets/delegate.css`.
- `custom_dir = "overrides"` in `[project.theme]`, for template and partial overrides. Zensical templates are MiniJinja and compatible with Material for MkDocs overrides.
- `extra_javascript`, only where CSS and templates can't do the job (see "Table labels").

Check every selector and template name against the Zensical version pinned in `.zensical-version`. Do not edit the docs *content* to fit the theme. In particular, `docs/reference/*.md` contains `generated:begin` / `generated:end` sections that `delegate docs` writes, and a test fails if they are changed by hand.

To view the prototype, open `design/delegate-docs-theme-2c.dc.html` in a browser, with `content.js` and `support.js` beside it. The strip at the top switches between Start here, Tutorial, Reference (`delegate run`), Glossary, Search and Mobile, and toggles light/dark. The strip is part of the mockup, not the theme.

## Fidelity
**High fidelity.** Colours, type, spacing and structure are final. Recreate them closely. Where Zensical's DOM makes an exact match impractical, keep the hierarchy and spacing ratios and say what you changed.

## Design tokens

Colours are oklch. Both schemes map onto the existing palette toggle: `default` is light and `slate` is dark. Set them as CSS custom properties on `[data-md-color-scheme="default"]` and `[data-md-color-scheme="slate"]`, then map them onto the Zensical `--md-*` variables (`--md-default-bg-color`, `--md-default-fg-color`, `--md-typeset-a-color`, `--md-code-bg-color`, `--md-code-fg-color` and so on).

| Token | Light | Dark | Used for |
|---|---|---|---|
| `bg` | `#ffffff` | `oklch(0.2 0.006 60)` | page background |
| `ink` | `oklch(0.27 0.01 60)` | `oklch(0.9 0.006 80)` | body text, h1 |
| `ink-soft` | `oklch(0.36 0.01 60)` | `oklch(0.82 0.006 80)` | admonition body, search snippets |
| `head` | `oklch(0.5 0.14 35)` (brick) | `oklch(0.75 0.12 40)` | h2, h3, NAME/SYNOPSIS labels, table labels, IMPORTANT label |
| `link` | `oklch(0.48 0.13 255)` | `oklch(0.76 0.1 255)` | links, NOTE label |
| `muted` | `oklch(0.52 0.01 60)` | `oklch(0.66 0.01 70)` | breadcrumbs, numbers, ¶, captions, th text |
| `rule` | `oklch(0.9 0.005 80)` | `oklch(0.32 0.008 60)` | all 1px rules and borders |
| `side` | `oklch(0.975 0.004 80)` | `oklch(0.23 0.006 60)` | sidebar background, guide table header |
| `code-bg` | `oklch(0.965 0.004 80)` | `oklch(0.25 0.006 60)` | inline code and code blocks |
| `code-ink` | `oklch(0.36 0.03 35)` | `oklch(0.85 0.05 40)` | inline code text, SYNOPSIS text, code in tables |
| `stripe` | `oklch(0.975 0.004 80)` | `oklch(0.23 0.006 60)` | alternate rows of reference tables |
| `mark` | `oklch(0.93 0.09 95)` | `oklch(0.45 0.08 95)` | search highlight background |

**Fonts (Google Fonts):**

- Noto Serif (400, 400 italic, 700) for body text.
- Open Sans (400, 600, 700) for headings and UI.
- Noto Sans Mono (400, 600) for code.

Zensical's `theme.font` takes only `text` and `code`. Set `text = "Noto Serif"` and `code = "Noto Sans Mono"`, then load Open Sans in an override or with `@import`, and apply it to headings and chrome in CSS.

**Radii:** 3px (inline code, version pill), 4px (code blocks, inputs, buttons). No shadows anywhere.

## Layout (desktop)
- **Header:** 56px tall, padding `0 28px`, 1px `rule` bottom border, background `bg`. From the left:
  - "delegate" in Open Sans 700 18px `ink`, then "Reference Guide" in Open Sans 400 15px `muted`, 8px gap, baseline-aligned.
  - A version pill "main": 12px, padding `2px 8px`, 1px `rule` border, radius 3, `muted`.
  - Pushed right with `margin-left:auto`: the repo link "AlexanderNZ/delegate", 14px `link`.
  - Drop the Zensical header's coloured primary bar. The header is plain `bg`.
- **Body:** a two-column grid, `290px | minmax(0,1fr)`.
- **Sidebar:** background `side`, 1px `rule` right border, padding `24px 20px 40px`, Open Sans 14px/1.5.
- **Content:** padding `40px 56px 64px`, inner `max-width: 760px`, left-aligned (not centred in the column).
- Hide the right-hand "On this page" TOC. The current page's h2s appear nested in the left sidebar instead.

## Components

### Sidebar
1. **"QUICK SEARCH"** label: Open Sans 700 11px, letter-spacing .08em, uppercase, `muted`. Below it, 6px gap, an input with placeholder "Search the guide": 14px, padding `7px 10px`, 1px `rule` border, radius 4, background `bg`. Margin-bottom 24px. This can wrap or trigger Zensical's search dialog. Moving search out of the header is part of the period feel.
2. **"TABLE OF CONTENTS"** label: same style as above, margin-bottom 10px.
3. **Numbered nav**, from the `nav` in `zensical.toml`:
   - **Top level:** "1. Start here", "2. Tutorial", "3. How-to guides", "4. Explanation", "5. Reference", "6. Glossary". Open Sans 600 `ink`. The number is in `muted`, min-width 20px, 6px gap. 6px between top-level groups.
   - **Children:** padding-left 26px, 13.5px, `link` colour, numbered "3.1." "3.2." in `muted` with min-width 28px.
   - **Current page:** its h2s are listed beneath it, 13px `muted`. They sit at padding-left 26px under a top-level page, or 60px under a child page. Top-level entries carry the page's section numbers ("1.1.", "1.2."); child entries carry the text only.
   - Use CSS counters on the nav lists for the numbers. Do not change the nav titles in `zensical.toml`.

### Page header (all pages)
- **Breadcrumb:** Open Sans 13px `muted`, items separated by "›" with a 6px gap, for example "delegate › Reference › delegate run". Margin-bottom 14px. Use the `navigation.path` feature if your Zensical version has it; otherwise use a partial.
- **h1:** Open Sans 400 38px/1.2, letter-spacing −0.01em, `ink`, margin `0 0 24px`, `text-wrap: pretty`. Code inside a title uses Noto Sans Mono at .85em.

### Headings
- **h2:** Open Sans 400 27px/1.25, `head` colour, margin `44px 0 14px`, padding-bottom 8px, 1px `rule` bottom border.
- **h3:** Open Sans 400 21px/1.3, `head`, margin `30px 0 10px`.
- **Section numbers:** h2 shows "1.", h3 shows "1.1.", in the same colour, 10px before the text, from CSS counters on `.md-typeset`.
  - **Exception:** no numbers on a page whose headings already start with a digit (the tutorial: "1. Install the kit").
  - Turn numbering off per page with front matter, for example `heading_numbers: false` on `tutorial.md`, read by a `main.html` override that adds a class to the article. If you'd rather not touch the tutorial, a body class keyed on the page URL works too.
- **Permalinks:** a "¶" after the heading text, 10px gap, `muted`, 20px on h2 and 16px on h3. Hidden (opacity 0) until the heading is hovered. `toc.permalink = true` is already set; set it to `"¶"` to get the symbol, then style `.headerlink`.

### Body text
- **Paragraphs:** Noto Serif 17px/1.7, margin `0 0 16px`, `text-wrap: pretty`.
- **Lists:** 17px/1.65, padding-left 24px (28px for `ol`), 8px between items.
- **Links:** `link` colour, no underline, underline on hover. A bold glossary term link (`[**harness**](glossary.md#harness)`) is bold and `link` colour.
- **Inline code:** Noto Sans Mono .84em, background `code-bg`, colour `code-ink`, padding `1px 5px`, radius 3. Code inside a link uses `link` colour with no background.

### Code blocks
- Background `code-bg`, 1px `rule` border, radius 4, margin-bottom 20px.
- `pre`: padding `18px 20px`, Noto Sans Mono 14px/1.55, `ink`, scrolls horizontally.
- **Language label:** in the top-right corner (top 6px, right 10px), for example "BASH" or "TEXT". Open Sans 600 11px, letter-spacing .06em, uppercase, `muted`. Read it from the highlight language class.
- Keep `content.code.copy`, but restyle the copy button as quiet `muted` text or an outline that doesn't collide with the label. One option is to show the label only when the block isn't hovered.

### Admonitions
The docs don't use admonitions yet. Style `!!! note` and `!!! important` / `!!! warning` so they're ready, and add `admonition` (plus `pymdownx.details` if you want) to the markdown extensions. **Don't** convert existing prose into admonitions. The mockup does that only to demonstrate the style.

- **Layout:** a grid of `96px | minmax(0,1fr)`, margin `4px 0 22px`. No background, no outer border, no icon.
- **Left cell:** the label "NOTE" or "IMPORTANT", centred vertically and horizontally, 1px `rule` right border, padding `10px 0`. Open Sans 700 12px, letter-spacing .1em. NOTE is `link` colour; IMPORTANT and WARNING are `head` (brick).
- **Right cell:** padding `10px 0 10px 20px`, Noto Serif 16.5px/1.65, `ink-soft`.

### Tables, guide pages
These apply on Start here, the tutorial, how-to, explanation and the glossary.

- Full width, collapsed borders, 1px `rule` border on every cell.
- **Cells:** 15px/1.55, padding `9px 12px`, top-aligned.
- **th:** background `side`, Open Sans 700 13.5px, left-aligned.
- **Code in cells:** .86em `code-ink`, no background, allowed to wrap (`overflow-wrap:anywhere`).
- **Wrapper:** `overflow-x:auto; overflow-y:hidden`, so a wide table never shows a vertical scrollbar.

### Reference pages (`docs/reference/*`)
Guide headings and body text are unchanged on these pages. Only these four things differ:

1. **Man header line:** first in the article, above the breadcrumb. It's a flex row with `space-between`: `DELEGATE-RUN(1)` · `delegate Manual` · `DELEGATE-RUN(1)`. Noto Sans Mono 13px `muted`, padding-bottom 10px, 1px `rule` bottom border, margin-bottom 18px.
2. **NAME / SYNOPSIS block:** directly after the h1, before the first paragraph.
   - A grid of `110px | minmax(0,1fr)`, gap `8px 16px`, padding-bottom 20px, 1px `rule` bottom border, margin-bottom 30px. No background and no box.
   - **Labels** "NAME" and "SYNOPSIS": Open Sans 700 11.5px/1.9, letter-spacing .12em, `head`.
   - **Values:** Noto Sans Mono 14px. NAME is `ink`; SYNOPSIS is `code-ink`.
3. **Table labels:** each table has a small label above it, the text of the nearest preceding h2 or h3 (for example "OPTIONS", "THE CONTINUATION"). Open Sans 700 11.5px, letter-spacing .12em, uppercase, `head`, margin-bottom 8px.
4. **Reference tables:**
   - No vertical borders. 1px `rule` top and bottom of the table, and 1px `rule` under the th row.
   - **th:** Open Sans 700 13.5px `muted`, no fill.
   - **Rows** alternate `bg` and `stripe`, with the first body row `bg`.
   - **Code in cells:** .86em `code-ink`, wrapping allowed.

**Man-page data.** Put it in the front matter of each reference page and read it in a `main.html` override (block `content` or `htmltitle`), so the generated sections stay untouched:

```yaml
---
man: DELEGATE-RUN(1)
man_name: delegate-run — build the tickets of a workflow
synopsis: delegate run [<workflow>] [--dry-run] [--resume <run-id>] [--break-lock] [--repo <repo>] [--tiers <tiers>]
---
```

Only the `delegate run` values come from the mockup. For the other reference pages, write NAME and SYNOPSIS from each page's own options table, and confirm them with the repo owner. Leave out a block if its front-matter key is missing. Front matter in `docs/reference/run.md` sits above the `# Reference:` heading and doesn't touch the generated sections, but run the test suite to confirm.

**Table labels.** CSS can't read a previous heading's text. Use one of these:
- A small `extra_javascript` (about 10 lines) that walks `.md-typeset table` on reference pages, finds the nearest previous `h2`/`h3`, and inserts `<div class="dlg-table-label">` with the heading text minus the "¶".
- If you'd rather avoid JS, drop the label when the table directly follows its heading.

Scope the script and the reference styles to reference pages with a body or article class from the override, for example `dlg-ref` when `page.meta.man` is set.

### Previous / next footer
- Turn on the `navigation.footer` feature.
- **Layout:** a grid of two equal columns, gap 16px, margin-top 56px, padding-top 20px, 1px `rule` top border. Open Sans.
- **Each side:** a 12px `muted` label ("← Previous" / "Next →") above a 15px `link` title. The right side is right-aligned.
- Below that, margin-top 36px: "Version main · Last updated 2026-10-09" in 12.5px `muted`. The date is optional; it needs a revision-date source. Drop this line if you can't fill it honestly.

### Search results
- Title "Search results". The query in an input with a "Search" button (Open Sans 600 14px, `side` background, 1px `rule` border, radius 4).
- A count line in Open Sans 14px `muted`: "6 pages match "verifier"."
- **Each result**, with 26px between results:
  - The title in Open Sans 600 18px `link`. If the hit is inside a section, "› Section" follows in 400 15px `muted`.
  - The file path in Noto Sans Mono 12.5px `muted`.
  - The snippet in Noto Serif 16px/1.6 `ink-soft`, with matches in `<mark>`: `mark` background, `ink` text, padding `0 2px`.
- Zensical's search is a modal. Restyle the modal's result items to this spec rather than building a separate results page.

## Responsive (≤ 76.25em, or wherever Zensical's drawer breakpoint sits)
- The sidebar becomes the drawer.
- **Header:** 52px, `side` background, padding `0 18px`. "delegate" in 700 17px and "Reference Guide" in 14px `muted`. A "Contents" button on the right that opens the drawer: Open Sans 600 13px, padding `6px 12px`, 1px `rule` border, radius 4.
- **Content padding:** `24px 20px 40px`. Type sizes stay the same.

## Interactions
- **Heading hover:** reveals ¶ (opacity 0 → 1, no transition needed).
- **Links:** underline on hover.
- **Palette toggle:** keep both palettes and the toggle as configured. Only the colours change.
- No animations.

## Config changes (summary)
```toml
[project]
extra_css = ["stylesheets/delegate.css"]
# extra_javascript = ["javascripts/delegate-table-labels.js"]   # if you use the JS approach

[project.theme]
custom_dir = "overrides"
font.text = "Noto Serif"
font.code = "Noto Sans Mono"
features = [ ...existing..., "navigation.footer", "navigation.path" ]   # path only if supported

[project.markdown_extensions]
toc.permalink = "¶"
admonition = {}
```
The palette entries keep `scheme = "default"` / `"slate"`. If Zensical's `variant` setting changes the base layout a lot, try `variant = "classic"` and pick whichever needs fewer overrides.

## Acceptance checks
- `zensical build` passes with the existing `invalid_links` and `invalid_link_anchors` validation.
- The repo's test suite passes. In particular, the `delegate docs` generated-section test still passes on the reference pages.
- In light and dark, body text meets 4.5:1 contrast, and so do the `muted` captions at their sizes.
- Each page has been checked against the prototype: Start here, Tutorial, `delegate run`, Glossary, search, and mobile width.

## Files
- `design/delegate-docs-theme-2c.dc.html`: the prototype. All values above come from this file.
- `design/content.js`: the mockup's content (verbatim excerpts from `docs/`) and the page and table logic, including how table labels pick the nearest heading.
- `design/support.js`: the runtime the prototype needs. It isn't part of the theme.

## Assets
No icons or images are used. Fonts come from Google Fonts: Noto Serif, Open Sans and Noto Sans Mono.
