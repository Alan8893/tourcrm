# TourCRM Header Scenes v1.0

**Status:** APPROVED BASELINE  
**Purpose:** production specification and asset hand-off for the decorative Header / Brand Zone illustrations.

## 1. Scope

TourCRM uses a unified illustrated Header / Brand Zone across the web application.

The approved illustration set consists of:

- **9 independent scenes**
- **2 formats per scene**
- **18 master PNG assets total**

The mobile assets are dedicated compositions. They must not be produced by simply cropping the desktop asset.

These illustrations are decorative assets only. They must not contain application UI, text, branding, navigation, buttons, status labels, or other interface elements.

---

## 2. Approved visual direction

All 18 assets form one visual series.

The series should communicate:

- tourism and hiking;
- nature;
- adventure;
- teamwork;
- friendly club atmosphere;
- interaction between instructor and participants;
- a modern, warm TourCRM product character.

### Visual language

- modern authored digital illustration;
- warm, friendly, polished appearance;
- soft rounded forms;
- natural outdoor palette;
- moderate detail;
- cinematic depth without photorealism;
- consistent character design across the series;
- playful enough for school-age users, but not childish;
- suitable for a modern European product interface.

### Do not introduce

- photorealistic photography;
- corporate stock illustration;
- generic SaaS illustration;
- office / business imagery;
- generic icon-library graphics;
- emoji;
- SVG-style flat icon compositions;
- aggressive contrast;
- neon / acidic colors;
- excessive visual noise;
- advertising-banner aesthetics.

---

## 3. Transparency and Header behavior

### Background

All approved master assets use a **transparent RGBA background**.

There must be no solid white, red, green, checkerboard, or other artificial background added to the asset.

The transparency is part of the composition and allows the illustration to sit naturally over the TourCRM Header / Brand Zone background.

### Desktop left-edge fade

Desktop assets include a **soft alpha transparency gradient from the left edge**.

The fade must be real alpha transparency, not a painted white/colored gradient.

The left side therefore becomes progressively transparent and should visually merge into the Header background.

Do not replace the alpha fade with a CSS color gradient unless explicitly approved.

### Mobile

Mobile assets are standalone compositions and do not rely on the desktop left-edge fade.

They must retain transparent RGBA background.

---

## 4. Technical master format

### Desktop

- Canvas: **1600 × 400 px**
- Format: **PNG**
- Color: **RGBA**
- Background: transparent
- Aspect ratio: 4:1
- Desktop composition is panoramic.
- Main characters / objects should stay away from the extreme edges.
- Central area should remain comparatively calm.

### Mobile

- Canvas: **800 × 500 px**
- Format: **PNG**
- Color: **RGBA**
- Background: transparent
- Aspect ratio: 8:5
- Dedicated mobile composition.
- Characters may be larger than in desktop.
- Secondary details may be reduced.
- Composition must remain clearly identifiable as the same scene.

### Important

PNG is the **approved master/source format for this asset hand-off**.

Do not convert these masters to SVG.

If a WebP production derivative is required later, it must be generated from the approved PNG masters without changing composition, transparency, dimensions, or visual appearance.

---

## 5. Approved visual scene set

The current basic theme contains these approved visual scenes:

| # | Scene | Desktop | Mobile |
|---|---|---|---|
| 01 | Forest Hike | Approved | Approved |
| 02 | Mountains | Approved | Approved |
| 03 | Camp | Approved | Approved |
| 04 | Campfire | Approved | Approved |
| 05 | Kayaks | Approved | Approved |
| 06 | Navigation | Approved | Approved |
| 07 | Crossing | Approved | Approved |
| 08 | Camp Morning | Approved | Approved |
| 09 | Finish | Approved | Approved |

These scene descriptions are a **design reference only**. The application does not need to know or store the semantic meaning of a particular sequence number. Future themes may contain a different number and composition of assets.

### Scene meanings

**01 — Forest Hike**  
Group of tourists moving along a forest trail with backpacks; beginning of an adventure.

**02 — Mountains**  
Tourists on a mountain route / viewpoint with a broad alpine landscape; route and exploration.

**03 — Camp**  
Tourist camp with tent, equipment and group activity / rest.

**04 — Campfire**  
Group gathered around a campfire; communication and team atmosphere are the primary subject.

**05 — Kayaks**  
Tourists travelling in kayaks on calm water; active outdoor recreation without extreme rafting.

**06 — Navigation**  
Instructor and group studying a map and compass; interaction and learning.

**07 — Crossing**  
Group crossing a natural obstacle together, with visible cooperation and mutual support.

**08 — Camp Morning**  
Morning at camp; participants prepare equipment / food and get ready to continue the route.

**09 — Finish**  
Group has completed the route and celebrates the shared result; tourism rather than a sports podium.

---

## 6. Asset naming contract

Use the following canonical naming pattern:

```text
<viewport>-<theme>-<sequence>.png
```

### Components

- `viewport`: `desk` or `mob`
- `theme`: lowercase ASCII theme slug, for example `basic`, `winter`, `ny`, `8mar`
- `sequence`: zero-padded numeric sequence inside the selected theme, for example `01`, `02`, `03`

Examples:

```text
desk-basic-01.png
desk-basic-02.png
desk-basic-03.png
...
desk-basic-09.png

mob-basic-01.png
mob-basic-02.png
mob-basic-03.png
...
mob-basic-09.png
```

Future thematic packages follow exactly the same contract:

```text
desk-winter-01.png
mob-winter-01.png

desk-ny-01.png
mob-ny-01.png

desk-8mar-01.png
mob-8mar-01.png
```

### Naming rules

- lowercase only;
- ASCII characters only;
- hyphen-separated components;
- no spaces;
- no scene names in filenames;
- no version suffix in individual filenames;
- no `desktop/` or `mobile/` directory component;
- sequence number is only a stable position inside a theme, not a semantic scene ID;
- the number of assets is not globally fixed and may differ between themes.

The application must not depend on `01` meaning Forest Hike, `05` meaning Kayaks, or any other specific scene. The files are treated as an ordered set belonging to a theme.

---

## 7. Repository location

Place approved source assets directly in the package directory:

```text
docs/
└── 06-ui/
    └── assets/
        └── packages/
            └── header-scenes-v1/
                ├── desk-basic-01.png
                ├── desk-basic-02.png
                ├── desk-basic-03.png
                ├── desk-basic-04.png
                ├── desk-basic-05.png
                ├── desk-basic-06.png
                ├── desk-basic-07.png
                ├── desk-basic-08.png
                ├── desk-basic-09.png
                ├── mob-basic-01.png
                ├── mob-basic-02.png
                ├── mob-basic-03.png
                ├── mob-basic-04.png
                ├── mob-basic-05.png
                ├── mob-basic-06.png
                ├── mob-basic-07.png
                ├── mob-basic-08.png
                └── mob-basic-09.png
```

Future themes are added to the same package directory using the same naming contract. For example:

```text
header-scenes-v1/
├── desk-basic-01.png
├── ...
├── mob-basic-09.png
├── desk-winter-01.png
├── ...
├── mob-winter-07.png
├── desk-ny-01.png
├── ...
└── mob-ny-12.png
```

This package is separate from the official brand package. Do not place these files into `brand/`.

---

## 8. Theme and asset selection

The asset package is designed for automatic Header rotation.

The application needs to know only:

1. which theme is active;
2. which viewport is required;
3. which asset sequence should be displayed according to the approved rotation algorithm.

The rotation algorithm is intentionally **not defined by this asset specification** and may be decided separately (for example, daily rotation, random rotation, deterministic rotation, or another approved strategy).

The application must not require a semantic scene catalog to perform selection.

### Theme configuration

If a system-level Header theme setting is introduced, it should store the theme slug only, for example:

```text
theme = basic
```

The asset path remains fixed. The selected theme is resolved through the naming contract.

Do not introduce a configurable filesystem path as part of the Header asset contract.

---

## 9. Header implementation rules

The Header / Brand Zone must use these assets as decorative visual content.

### Desktop

Use the desktop asset according to the available Header width.

The same desktop asset must work across:

- wide-format monitor;
- standard desktop / laptop;
- tablet landscape where the desktop Header variant is used.

Do not create separate assets for individual monitor widths.

The browser must not require a unique illustration for 3440×1440, 1920×1080, etc.

### Mobile

Use the dedicated mobile asset at the mobile breakpoint.

Do not use the desktop asset as a mobile crop.

Do not stretch the desktop asset vertically to imitate the mobile version.

### Functional Header elements

The illustration is a decorative layer.

It must not cover or intercept functional controls such as:

- avatar / profile menu;
- navigation controls;
- mobile `Меню`;
- other interactive Header controls.

Interactive elements remain above the decorative layer and retain their existing behavior.

The decorative image must not become an interactive element.

---

## 10. Composition safety

The Header must remain readable when the illustration is rendered behind / alongside interface content.

Do not add text to the illustration to compensate for available UI space.

Do not move, repaint, crop, or recolor an approved master during implementation without a new visual approval.

Do not introduce CSS masks or overlays that materially alter the approved artwork.

---

## 11. Acceptance checklist

The asset package is accepted only when all of the following are true:

- [ ] The current basic package contains exactly 18 PNG files.
- [ ] The current basic package contains 9 desktop assets and 9 mobile assets.
- [ ] Every desktop asset is **1600×400**.
- [ ] Every mobile asset is **800×500**.
- [ ] All assets are RGBA PNG.
- [ ] All assets have transparent background.
- [ ] Desktop assets have the approved soft alpha fade from the left edge.
- [ ] No text is present in the artwork.
- [ ] No TourCRM logo is present in the artwork.
- [ ] No UI controls are present in the artwork.
- [ ] No SVG assets are introduced.
- [ ] Mobile assets are dedicated compositions, not desktop crops.
- [ ] Filenames follow `<viewport>-<theme>-<sequence>.png`.
- [ ] No scene name is required in filenames.
- [ ] No `desktop/` or `mobile/` subdirectories are required.
- [ ] Future themes can contain a different number of assets without changing the naming contract.
- [ ] All 18 current assets remain visually consistent as one basic series.
- [ ] Header controls remain functional and clickable.
- [ ] The decorative layer does not intercept pointer events.
- [ ] Desktop and mobile rendering are visually checked in the actual TourCRM Header.
- [ ] No unapproved visual modifications are introduced.

---

## 12. Important implementation boundary

This document defines the **approved visual asset contract**.

Frontend implementation must not:

- redesign the illustrations;
- generate alternative artwork;
- replace them with generic icons;
- invent additional Header scenes;
- add text or logo overlays inside the image;
- create monitor-specific illustration variants;
- substitute SVG;
- alter the approved visual language;
- make business logic depend on the semantic meaning of a sequence number.

Any change to the visual assets requires a new visual approval before implementation.
