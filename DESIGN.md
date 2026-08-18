# Design blueprint

Extracted from the NFR AI Asset Library reference and applied to this app. This
is the working spec — change values here and in `static/styles.css` together.

## Tokens

```css
--page-bg:      #06001A   /* near-black deep violet, the page plane */
--purple:       #A100FF   /* primary accent */
--purple-600:   #8A15E0   /* pressed / print accent */
--violet-deep:  #3D0066   /* gradient origin */
--pink:         #FF50C8   /* secondary accent: eyebrows, gradient text */
```

Surfaces are **translucent white over the page plane**, never opaque fills:

| Role | Value |
|---|---|
| Card | `rgba(255,255,255,.05)` |
| Card border | `rgba(255,255,255,.08)` |
| Raised / input | `rgba(255,255,255,.06)` |
| Hover | `rgba(255,255,255,.11)` |

Text is white at decreasing opacity — never a grey hex:

| Role | Value |
|---|---|
| Primary | `#fff` |
| Body | `rgba(255,255,255,.62)` |
| Muted | `rgba(255,255,255,.40)` |
| Faint (eyebrow) | `rgba(255,255,255,.25)` |

Status: green `#2DBF82` (live/good), amber `#E0A000` (pending/locked), red
`#FF6B6B` (error). Reserved for state — never used as a series colour.

## Typography

Three faces, each with one job:

| Face | Job | Treatment |
|---|---|---|
| **Space Grotesk** 700 | headings, hero, stat values | `letter-spacing: -.02em` |
| **Inter** 400/500/600 | body, buttons, tables | default |
| **JetBrains Mono** 700 | eyebrows, tags, badges, metadata | 8–10px, UPPERCASE, `letter-spacing: .1–.24em` |

The mono treatment is the signature of this system. Small uppercase mono labels
mark every section, tag and metadata line — that, plus the purple glow, is what
makes it recognisable.

## Shape and depth

| Element | Radius |
|---|---|
| Card / panel | 20px |
| Button | 11px |
| Pill / tag | 20px (full) |
| Small badge | 5px |
| Icon tile | 12px |

Depth is a **purple glow**, not a grey drop shadow:

```css
box-shadow: 0 0 50px rgba(161,0,255,.12), 0 20px 48px rgba(0,0,0,.5);
```

Primary buttons carry a gradient: `linear-gradient(135deg, #3D0066, #A100FF)`.
Hover lifts (`translateY(-1px)` buttons, `-4px` cards) at `.16s–.22s ease`.

## Signature elements

**Background orbs.** Large blurred radial gradients (`filter: blur(90px)`) fixed
behind everything, drifting slowly. They give the dark plane depth without
competing with content.

**Gradient text.** Hero emphasis uses
`linear-gradient(90deg, #CC88FF 5%, #FF50C8 90%)` with `background-clip: text`.

**Grid texture.** 24px grid of `rgba(255,255,255,.04)` lines on feature panels.

**Eyebrow rule.** Mono eyebrow labels are preceded by a 20×2px accent bar.

## Charts

Charts need their own treatment because they appear in two places with opposite
backgrounds, so they are rendered **twice** rather than recoloured by CSS:

| Context | Surface | Series | Verdict |
|---|---|---|---|
| Web UI | `#12121F` | `#A100FF` | validated, passes all checks |
| PDF export | `#FCFCFB` | `#8A15E0` | validated, passes all checks |

Both were checked with the data-visualisation validator against their actual
surface — contrast, lightness band and chroma floor. The lighter violet
`#C77BFF` was rejected: it fails the lightness band on the dark surface.

Dark mode here is *selected*, not an inversion of the light theme.

The PDF stays light on purpose. A dark-themed report wastes ink and reads wrong
when printed, which is what an MI report is for.

Chart rules unchanged from before: one measure per chart means single-series, so
one hue and no legend; a scalar renders as a headline number; no pie.
