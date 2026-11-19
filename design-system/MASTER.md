# Formwork design system (MASTER)

Source of truth for `apps/web`. Tokens and component classes are ported from `docs/prototype.html` into
`apps/web/app/prototype.css`; production-only additions live in `apps/web/app/globals.css`. Do not introduce a colour,
font or radius that is not listed here. Change this file first, then the CSS.

Product character: a dense, calm B2B console for finance operations. The reviewer's job is to compare a value with
its source, so the source region and the value are always on screen together. Colour is reserved for state.

## Tokens

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--bg` | `#F3F4F7` | `#0E1016` | Page |
| `--surface` | `#FFFFFF` | `#161922` | Cards, inputs |
| `--sunken` | `#ECEEF3` | `#11141B` | Wells, table headers |
| `--line` | `#DDE1E9` | `#283040` | Borders |
| `--ink` | `#141824` | `#E7EAF2` | Text |
| `--muted` | `#576074` | `#9CA4B8` | Secondary text (AA on bg and surface) |
| `--accent` / `--accent-soft` | `#2742C9` / `#E6EAFB` | `#8197FF` / `#1E2747` | Primary action, selection, "approved by you", evidence highlight |
| `--good` / `--good-soft` | `#23794A` / `#E3F2E9` | `#5CC48A` / `#15291F` | Auto-approved, completed |
| `--warn` / `--warn-soft` | `#9A5B00` / `#FCF0D9` | `#F0B44C` / `#2E2412` | Needs review, weak description, missing |
| `--crit` / `--crit-soft` | `#B23A12` / `#FBE6DE` | `#FF8A62` / `#331A12` | Sources disagree, failed, errors |

Type: `--font-display` Schibsted Grotesk (headings, big numbers), `--font-body` IBM Plex Sans 15/1.5, `--font-mono`
IBM Plex Mono (field paths, locators, quotes, code). Numbers use `tabular-nums`. Radius `--radius` 10px (cards),
8px controls, 7px cells. One shadow: `--shadow`. Dark mode follows `prefers-color-scheme`.

## States (always colour + icon or label)

| State | Colour | Icon | Label |
| --- | --- | --- | --- |
| Auto-approved | good | check | "Auto-approved" |
| Needs review / low confidence / missing | warn | triangle | "Needs review" / "Missing" |
| Sources disagree | crit | cross | "Sources disagree" |
| Approved by a reviewer | accent | check | "Approved by you" |

Grid cells also carry the state in `aria-label`; the inspector repeats it as a chip with text.

## Components

Top bar + five-step stepper (Target format, Sources, Run, Review, Export) · `card` · `btn` (`primary`, `ghost`) ·
`seg` segmented control · `chip` · schema `ftable` with inline weak-description warning · `drop` zone and `src` cards ·
`pipeline` stages and `metric` tiles · review `summary` tiles with the threshold slider · record grid `rg` of `cell`
buttons (value, state dot, confidence bar) · inspector `insp` (value, confidence meter with threshold tick, source
tabs, viewer, quote, checks, alternatives, actions) · `export` options + preview.

Viewer: digital PDFs render with PDF.js; scans and photos show the stored deskewed page image (OCR boxes refer to
it); sheets show the cited rows with the row highlighted; email/docx show the block text with the quote marked.
The highlight is an accent outline with an 18% fill (crit for an alternative's evidence).

## Rules

- Layout breakpoints: two-column review above 1100px, single column below; verified at 1440px and 390px with no
  horizontal page scroll (wide tables scroll inside their own container).
- Motion: 120-180ms ease-out on cell focus and highlight moves only. Everything is disabled under
  `prefers-reduced-motion`.
- Focus: 2px accent outline on every interactive element; the grid, tabs and actions are reachable by keyboard.
  Review shortcuts: `A` approve, `E` edit, `N` next issue.
- Copy: plain verbs ("Approve", "Edit value", "Use this value"); say what happened and what to do next in errors.
- Anti-patterns: no colour-only status, no gradients or decorative icons, no modal for a single-field edit, no
  toast as the only record of an action, no fonts or colours outside the token table.
