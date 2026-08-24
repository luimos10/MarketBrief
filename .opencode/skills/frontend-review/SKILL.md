---
name: frontend-review
description: Revisión frontend de UI, UX, responsive, accesibilidad y regresiones visuales en reportes HTML de Market Brief. Usar al revisar cambios visuales o preparar una interfaz para publicar.
---

# Frontend Review For Market Brief

Use this skill to review the visual implementation of Market Brief. Do not edit
files unless the user explicitly requests fixes after the review.

## Scope

Review the HTML generator in `modules/delivery.py` and, when relevant, a
generated `output/brief_*.html`. Account for the report's actual use: traders
must scan dense, time-sensitive information on desktop and mobile.

## Review Checklist

### Information Hierarchy

- Confirm that market session/status, overall bias, primary asset data, and
  risk cues are easy to identify before long-form analysis.
- Flag labels, values, or warnings that are visually ambiguous or too subtle.
- Check that headings create a sensible report outline.

### Responsive Behavior

- Inspect narrow mobile and desktop behavior.
- Flag clipped content, horizontal overflow, unreadable dense tables, broken
  grids, overlapping controls, and charts that cannot be interpreted on a
  small screen.
- Confirm that mobile adaptations preserve key prices, bias, and risk data.

### Accessibility

- Check text contrast on the dark palette, including muted text and borders.
- Check that bull, bear, neutral, warning, and loading states have a text or
  structural cue in addition to color.
- Check semantic heading order, table headers, alt text for meaningful images,
  and visible keyboard focus for interactive elements.
- Flag hover-only information and motion that lacks a reduced-motion path.

### Consistency And Regressions

- Check use of the CSS variables and shared component patterns.
- Flag one-off colors, spacing, typography, borders, or radii that diverge
  from the design system without a reason.
- Check that changes to the HTML generator do not alter report content,
  calculations, Telegram delivery, or the dependency-free rendering approach.

## Findings Format

Report findings first, ordered by severity:

1. Critical: blocks reading essential market information or produces a broken
   report.
2. High: materially harms mobile use, accessibility, or interpretation of a
   trading signal.
3. Medium: inconsistent or confusing behavior with a practical impact.
4. Low: polish or maintainability improvement.

For each finding, include the file and line number when available, the impact,
and a concise recommended fix. If no findings are present, state that clearly
and name any verification gaps, such as not being able to render the report.

## Boundaries

- Do not modify files during a review unless explicitly asked.
- Do not treat a generated HTML file as the source of truth; findings should
  point to `modules/delivery.py` when applicable.
- Do not report stylistic preferences as defects unless they hurt consistency,
  readability, responsive behavior, accessibility, or maintainability.
