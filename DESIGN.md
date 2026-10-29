# Design: buy-smarter desktop app

**World: "lab report".** The app reads like a consumer test-lab protocol: cool ink neutrals,
white protocol cards on a pale ground, one teal accent (Kazakhstan's sky blue, darkened for
contrast), verdicts in semantic colors. Calm and evidential; the report's numbers lead.

Source of truth: `src/buysmarter/gui/theme/__init__.py` (palettes, scales, generated QSS).
Screenshots: `docs/screenshots/desktop/`.

## Tokens

| Token | Light | Dark | Use |
|---|---|---|---|
| window | `#F4F6F8` | `#11161C` | page ground |
| surface | `#FFFFFF` | `#192028` | cards, inputs, tables |
| sidebar | `#E9EDF1` | `#141A21` | sidebar, table headers, bar track |
| border / border_strong | `#D3D9E0` / `#848F9B` | `#2B3540` / `#5E6B78` | dividers / input outlines (3:1) |
| text / text_muted / disabled | `#16202B` / `#4A5663` / `#5E6975` | `#E5EAF0` / `#A2AEBB` / `#8592A0` | text |
| accent / accent_hover / on_accent | `#00758A` / `#005F70` / `#FFFFFF` | `#3FB8CC` / `#68CAD9` / `#08171B` | primary action, score, focus, bars |
| accent_soft | `#DCEFF2` | `#173A42` | selection, current nav item |
| ok / warning / danger | `#1D7135` / `#875200` / `#B3261E` | `#5CC27C` / `#E0AE4C` / `#F28B82` | verdicts, statuses |

Every text/background pair is held to WCAG AA 4.5:1 by `tests/gui/test_theme.py`.

**Type:** Inter (bundled as static 400/600/700: Qt renders a variable font's weights as Regular), one family. 12 caption · 14 body · 16 card title ·
20 product title · 24 score and price · 28 screen heading. **Spacing:** 4px grid (4, 8, 12, 16, 24, 32).
**Radius:** 6 (controls), 8 (cards).

## Components

- Sidebar (220px): the app name with its cart icon, then screens in two groups, ПОКУПКИ
  (Поиск, Отчёт, Прошлые поиски, История цен) and СЕРВИС (Настройки поиска, Состояние и
  ключи). Group labels are 12/600 muted caps; items carry an 18px icon.
- Search screen: a 28/700 heading with a muted hint, then one form card (`QFrame#card`,
  radius 10): the 16px query with a search icon, city and budget side by side under 12px
  captions, the marketplaces with their live status, AI on the right, progress and buttons
  at the bottom with Найти last. Under it, up to three recent distinct queries from the saved
  reports, each with a flat accent «Повторить».
- Every field (line edit, combo box, spin box) is 36px tall.
- Cards are `QGroupBox`es: surface, 1px border, radius 10, the 16/600 title inside the card
  at the top left; a card's own layout has zero margins (the QSS padding frames it).
- Every screen opens with a 28/700 heading: the search asks «Что ищем?», the report shows the
  query with the parsed constraints under it, the others show their nav title.
- Tables: no vertical grid lines, rows ruled by a 1px border, left-aligned 12/600 headers.
- One primary button per screen (`primary="true"`): Найти, Сохранить, Показать историю,
  Открыть на маркетплейсе. Everything else is secondary.
- Statuses are labels with `state` = done/ok (green), running (accent), failed/missing (red),
  skipped/waiting (muted), warning (amber); set with `theme.set_state`.
- Tables show every row (`gui/tables.py`); the page scrolls, never the table. Headers are
  12/600 muted on the sidebar tone.
- Axis scores pair the CLI's `score/max` text with an 8px bar, 160px wide.
- Past searches render the saved markdown in the theme (`style_report`): 20/16 bold headings,
  accent links, ruled tables.
- Light or dark follows the OS and switches live.

## Rules

- Icons are Material Design line icons (`mdi6.*` via qtawesome, `theme.icon`), muted by default
  and accent when selected; no emoji in chrome. The `⚠` barred marker is a CLI term the report shares.
- Accent marks action, current selection and the score only, never decoration.
- Report wording stays the CLI's ("Best pick", "Score", "Alternatives", "Отсеяно").
