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
Between cards 24 (`PAGE_GAP`: never less than a card's own padding, or the cards run together);
between rows inside a card 12 (`CARD_GAP`, set by `card_body`). **Prose:** a paragraph
(`theme.Prose`) stops at 560px, about 70 characters, however wide the window; a status beside
buttons stays a plain label.
**Radius:** 6 (controls), 8 (cards).

## Components

- Sidebar (220px): the app name with its cart icon (accent, redrawn on a scheme change), then
  screens in two groups, ПОКУПКИ (Поиск, Отчёт, Прошлые поиски, История цен) and СЕРВИС
  (Категории, Настройки поиска, Стиль, Состояние и ключи). Group labels are 12/600 muted caps;
  items carry an 18px icon.
- Search screen: a 28/700 heading with a muted hint, then one form card (`QFrame#card`,
  radius 10): the query with a search icon (a field like any other), city and budget side by side under 12px
  captions, the marketplaces with their live status, AI on the right, progress and buttons
  at the bottom with Найти last. Under it, up to three recent distinct queries from the saved
  reports, each with a flat accent «Повторить». A clarifying question replaces them: its
  answers are a left-aligned list at regular weight, then the own-answer field.
- Every field (line edit, combo box, spin box) is 36px tall.
- Cards are `QGroupBox`es: surface, 1px border, radius 10, the 16/600 title inside the card
  at the top left; a card's own layout has zero margins (the QSS padding frames it).
- Every screen opens with a 28/700 heading: the search asks «Что ищем?», the report shows the
  query with the parsed constraints under it, the others show their nav title. An empty state
  keeps the heading and puts one muted line under it.
- Everything fits the minimum window (960×640): a screen that does not fit scrolls instead of
  squeezing its rows.
- Tables: no vertical grid lines, rows ruled by a 1px border, left-aligned 12/600 headers.
- One primary button per screen (`primary="true"`): Найти, Сохранить, Показать историю,
  Открыть на маркетплейсе (secondary while the «Изучить категорию» banner, the primary then,
  shows). Everything else is secondary.
- Statuses are labels with `state` = done/ok (green), running (accent), failed/missing (red),
  skipped/waiting (muted), warning (amber); set with `theme.set_state`.
- Tables show every row (`gui/tables.py`); the page scrolls, never the table. Headers are
  12/600 muted on the sidebar tone, indented so cell text sits under them. A stretched text
  column (a title) keeps at least 160px; past that the table scrolls sideways. A column empty
  on every row is hidden («Родитель» on «Категории»). The photo grid on «Стиль» follows the
  same rule, as tall as its rows, with square 96px tiles cut from each photo's centre.
- Settings forms (`theme.form_rows`) put each label left of its field, centred on the field's
  first 36px line.
- The report's best pick shows three figures at 24px with 12px captions on one line: the score
  (700, accent), the confidence and the price (600). «Настроить» under the heading is an inline
  link on the heading's vertical.
- A category switch offers «Сбросить к автоматическому» only once the user has made a choice.
- Axis scores pair the CLI's `score/max` text with an 8px bar, 160px wide, on a border-tone
  track (visible on the dark card).
- Past searches render the saved markdown in the theme (`style_report`): 20/16 bold headings,
  accent links, ruled tables, body lines at 150% leading; `_note_` lines muted, long hashes and
  URLs wrapped. The list on the left shows the date over the query, so the query gets the full
  row.
- Light or dark follows the OS and switches live.

## Rules

- Icons are Material Design line icons (`mdi6.*` via qtawesome, `theme.icon`), muted by default
  and accent when selected; no emoji in chrome. The `⚠` barred marker is a CLI term the report shares.
- Accent marks action, current selection and the score only, never decoration.
- Report wording stays the CLI's ("Best pick", "Score", "Alternatives", "Отсеяно").
- The same rules on every screen: colours, sizes and gaps come only from the theme's tokens;
  `tests/gui/test_theme.py` fails on a colour literal, an inline style or a raw pixel size
  outside `gui/theme/`. The general rules behind them (UX laws, typography, grid) are in
  octrow-skills `plugins/qt-ui/skills/qt-ui/references/ux-rules.md`.
- Checked at three window sizes: 1280×800, the minimum 960×640 and maximised on a 1440p
  monitor (2560×1440, content capped at 1200 and centred).
