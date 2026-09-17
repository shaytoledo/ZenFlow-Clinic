# Point Card Design (Phase 4.2)

How the treatment page presents an acupuncture point formula, and why. This is the research
step of Phase 4.2 in `docs/MASTER_PLAN_EN.md`. It was written before the redesign and is kept
up to date as 4.2b and 4.2c land.

## 1. What a practitioner needs from a point, during a session

Clinical references agree on the shape of a point entry:

- **Identity:** the WHO standard names a point in three parts: an alphanumeric code derived from
  the English channel name (`LI4`), the Pinyin name (*Hegu*) and the Han characters (合谷).
  English translations ("Joining Valley") are common but not part of the standard.
  *Source: WHO, A Standard International Acupuncture Nomenclature (1991).*
- **Entry sections:** Deadman's *A Manual of Acupuncture* is the usual teaching reference, and
  each entry has location, needling, actions, indications, commentary and cautions. The cautions
  on needling are placed "where they are readily visible", next to the needling notes, not at
  the end. *Source: Eastland Press, A Manual of Acupuncture (2nd ed.).*
- **Pregnancy:** a few points are traditionally avoided in pregnancy.
  - Across 28 classical texts, the LI4 + SP6 combination and CV4 were contraindicated
    consistently.
  - Points used to promote labour (LI4, SP6, BL67, BL60, GB21, CV3, …) are treated with
    caution as well.
  - Teaching material often names "five forbidden points": LI4, SP6, BL60, GB21 and CV3 (or
    BL67, depending on the source).

  *Sources: A Traditional Literature Review on Acupuncture and Moxibustion during Pregnancy
  (J Acupunct Meridian Stud, 2012); Yo San University, "What are the 5 forbidden acupuncture
  points?".*

During a session the therapist scans the whole formula, then works through it point by point.
From that, the order of importance on a card is:

1. **Code**: what they say, write and needle by. It is the largest element.
2. **Name**: Pinyin, plus Han characters when the data has them (Phase 4.3).
3. **Channel**: the colour helps scanning, but the channel *name* is always shown too, so colour
   never carries meaning on its own.
4. **Cautions**: always visible on the card face (see "Pregnancy" above), never behind a
   disclosure.
5. **Location**: one line; the full text is in the details.
6. **Why this point for this patient**: the AI's rationale, visually distinct from reference
   data, because it is a suggestion and not a fact.
7. **Secondary details**: actions, needling and the full location, behind a native
   `<details>` element.

## 2. What was wrong with the old card

- **No hierarchy:** the code, name, channel, location and actions were stacked as similar
  small lines.
- **Colour-only channel badge:** the code badge's colour was the only channel cue, and the
  channel chip disappeared when the name was missing.
- **Hidden cautions:** the LI4 caution sat at the end of its actions text.
- **Adding points:** a 28 px "+" was the only way to add a point, and it only added. There was
  no selected state, no count, no way to remove from the card, and no undo.
- **Borders and grid:**
  - card separators were drawn with `border-right` / `border-bottom` on every cell (a border
    hack, and wrong in RTL);
  - the grid used `auto-fill`, so it left empty columns.
- **Contrast:** teal `#0D9488` and grey `#9CA3AF` were used for small text, at 3.7:1 and
  2.5:1 on white. WCAG AA needs 4.5:1.
- **Dead point panel:** the point info panel was built but never opened (`openPointPanel` had
  no caller).

## 3. The design

### Tokens (`static/css/tokens.css`, every page)

- **Scales:** spacing is a 4 px scale; there are radius, type and shadow scales.
- **Semantic colours:** surface, border, text, accent, caution and danger.
- **Channel palette:** each channel has three tokens: soft background, border and ink.
- **Text contrast:** every text/background pair is at least 4.5:1, and a test checks this for
  both themes.
- **Dark mode:** it is a token override under `[data-theme="dark"]`. It is opt-in: the rest of
  the app is not dark-ready yet, and following `prefers-color-scheme` automatically would give
  half-dark pages.
- **Naming:** tokens are prefixed `--zf-` so they never collide with the older `--primary`,
  `--border` and similar variables in `style.css`.

### Card anatomy

```
┌──────────────────────────────────────────┐
│ [LR3]  Taichong                  [+ Add] │  code badge (channel colours) · name · toggle
│ ◉ Liver                                  │  channel chip, icon + name
│ ⚠ Traditionally avoided in pregnancy     │  only for the caution list
│ 📍 Dorsum of foot, between 1st and 2n…   │  one line
│ ┃ FOR THIS PATIENT                       │  AI rationale, accent rail
│ ┃ Subdues Liver Yang (stress headache)   │
│ ▸ Actions & needling                     │  <details>: actions · needling · location
└──────────────────────────────────────────┘
```

- **Semantics:**
  - each card is an `<article>` with an accessible name ("LR3 Taichong");
  - the name is an `<h3>` under the section's `<h2>`;
  - the details are native `<details>` / `<summary>`, so they need no JS and work with the
    keyboard;
  - decorative icons are `aria-hidden`.
- **RTL:** layout uses logical properties only (`inline-start`, `inline-end`), so `dir=rtl`
  mirrors the card by itself and there are no `row-reverse` double flips (commit 2c00db6).
- **Grid:**
  - `repeat(auto-fit, minmax(min(100%, 240px), 1fr))` with a real `gap`, and each card is its
    own bordered box;
  - rows stretch, so cards in a row share a height;
  - the location is clamped to one line so rows stay even.

### Selection model

- **Toggle:** a card's toggle button (`aria-pressed`) adds the point to today's points or
  removes it, and so does a click anywhere on the card outside the details. The compact chips
  in the "Points used" card are the same toggle.
- **Selected state:**
  - there is an accent ring and a tinted background;
  - the toggle reads "✓ Added" instead of "+ Add", so the state does not rely on colour;
  - chips show a check.
- **Running count:** "2 of 6 selected", in the section header, in a polite live region.
- **Undo:** any removal (card, chip, tag ×, or Backspace in the input) shows "Removed LR3 ·
  Undo" for 6 s. Undo puts the point back where it was.
- **Tags:** the tag label opens the point info panel, which is now reachable. The panel is a
  labelled dialog region that Escape closes. The tag's × button has an `aria-label`.

### Data notes (Phase 4.3a)

- **Kidney code:** the AI prompt and the old `POINT_INFO` used `KD3` for Kidney 3, but the WHO
  code is `KI3`. The `acupoints` table stores `KI3` with the alias `KD3`, and lookups accept
  both.
- **Yintang:** `YIN` duplicated `YINTANG` (EX-HN3; GV29 in the 2006 WHO locations). It is now
  one row with those aliases.
- **Cautions:** pregnancy cautions come from the table's `contraindications` column. BL60, BL67,
  CV3 and CV4 were given reference rows so their caution has a source. A caution is traditional
  guidance shown to a licensed therapist, not medical advice. The texts are the clinic's own
  summaries (`source`, `licence` columns); a vetted dataset can replace them through the seed
  file.

## 4. States of the AI points area (Phase 4.2b)

Before this change the AI points area had no single owner:

- a progress bar that moved on timers (25 % at 2.2 s, 55 % at 5 s…) whatever the run was
  actually doing;
- a Generate prompt and a Retry prompt, each drawn into the chip row of another card;
- a hidden section that appeared only once points existed;
- seven copies of the "remember the rationales" loop, one per code path.

Now `static/js/treatment/point-states.js` owns the area. Every code path (page load,
Generate, Update Diagnosis, Regenerate, Cancel, the follower) calls `showPointState(state)`.

| State | When | Shows |
|---|---|---|
| `idle` | nothing generated yet | empty panel ("No AI formula yet", or "No intake on file") + **Generate** |
| `loading` | a run is working, no point yet | step list + 6 skeleton cards |
| `partial` | batch A is in, batch B is working | the cards + 2 skeletons ("Selecting more points…") |
| `ready` | the formula is complete | the cards; "AI Formula" badge |
| `failed` | FAILED, or a run that finished with nothing | the cards it saved, if any + an alert panel + **Retry** |
| `cancelled` | the therapist cancelled | the cards that arrived before, if any + **Generate** |
| `stalled` | no result after 15 min of following | the cards shown + an alert panel + **Retry** (may override a stale status) |

- **Progress is real:**
  - the step list (Intake summary → TCM diagnosis → First points → More points) follows
    `points_status` (`GENERATING_STAGE_0/1/2A/2B`);
  - there are no timers and no percentages;
  - the list itself is decorative (`aria-hidden`);
  - "Step 2 of 4: TCM diagnosis…" is a live `role=status` line;
  - the section carries `aria-busy` while a run works;
  - failures use `role=alert`;
  - skeletons are `aria-hidden` and stop shimmering under `prefers-reduced-motion`.
- **Single mapping:** `stateOfNotes(notes)` is the one mapping from a notes object to a state,
  and it is unit-tested for every status.
- **Follower:**
  - `_pollForPoints` applies the notes it was given immediately, so opening a generating
    session no longer waits 2 s for the first poll;
  - it re-renders only when the state, stage or points change, so open details and focus stay
    where they are;
  - responses that arrive after a Cancel or a new run are dropped (a follow token).
- **Cancel:** re-reads the notes, because points from batch A are kept by the server.

## 5. Density (Phase 4.2d)

A segmented **Detailed / Compact** toggle sits in the section header (`role=group`, buttons
with `aria-pressed`).

- **Compact cards:** they keep the code, name, toggle, channel and pregnancy caution. They hide
  the location, the rationale and the details. The grid switches to 180 px `auto-fill`, so
  compact cards stay narrow.
- **Saving:** the choice is saved per therapist in `therapists.ui_prefs`
  (`PATCH /api/my/preferences`, whitelisted values only). It reaches the page in the config
  island, so the first paint is already right.
- **Tests:** a test keeps compact from ever hiding identity, selection or cautions.

## 6. Small screens and print (Phase 4.2c)

**Shell.** The 232 px sidebar never collapsed, so on a phone the page content was less than
100 px wide. Below 900 px the sidebar is now a drawer (`static/css/shell.css`,
`static/js/shell.js`, on every page):

- **Menu button:** in the topbar, with `aria-controls` / `aria-expanded` and a translated label.
- **Opening:** the drawer slides in from the reading side (from the right in RTL), a scrim sits
  behind it, and focus moves into it.
- **Closing:** Escape, the scrim, or following a link. Escape returns focus to the button.
- **Motion:** respects `prefers-reduced-motion`.
- **Topbar:** it drops the clock below 900 px and the page actions below 600 px (the same links
  are in the drawer).
- **Treatment page:** its two columns stack below 900 px, and its padding shrinks below 600 px.

**Print.** Every page prints without the sidebar and topbar, and without the fixed-height,
`overflow: hidden` layout that used to clip printing to one page. The treatment page prints only
its **patient handout** (`static/js/treatment/handout.js`), which is rebuilt right before every
print (the topbar "Print handout" button or Ctrl+P). It contains:

- the patient, date and therapist;
- the points used today, with reference names and locations in the page language;
- the recommendations left switched on.

`handoutPoints()` takes codes only, so nothing therapist-facing can reach the patient's copy: no
AI rationale, no diagnosis certainty, no notes.

## 7. Visual snapshots (Phase 4.2e)

`tests/e2e/test_visual_snapshots.py` photographs the AI points section at 375, 768 and 1280 px,
in English and Hebrew, in light and dark tokens. It also captures the whole viewport at each
width, plus the phone with the drawer open.

- **Baselines:** per-platform PNGs in `tests/e2e/snapshots/`, compared with a 0.2 % pixel
  tolerance.
- **Updating:** `ZF_UPDATE_SNAPSHOTS=1 python -m pytest tests/e2e`, then review the images before
  committing.
- **What they have caught:** the first run showed the section header squeezing into three lines
  on a Hebrew phone. The header now wraps (title on one line, pill and density toggle under it).
