---
name: design-system
description: Sistema de diseño, tokens visuales, colores, tipografía, espaciado, componentes, badges, tablas y gráficos de Market Brief. Usar al definir o estandarizar reglas visuales compartidas.
---

# Design System For Market Brief

Use this skill for reusable visual rules and components, not for isolated page
layout changes.

## Product Context

The HTML report is generated in `modules/delivery.py`. Its current design
system uses CSS custom properties in `:root`, a dark neutral surface scale,
off-white text, a single functional accent, and three financial state colors:
`--bull`, `--bear`, and `--neutral`.

Changes must improve consistency across generated reports without changing
financial calculations or report semantics.

## Workflow

1. Inspect the current `:root` variables and all existing consumers before
   introducing or renaming a token.
2. Reuse an existing token or component style when it fits.
3. Add a token only when a visual role is used repeatedly or represents a
   stable semantic meaning.
4. Update all relevant consumers in the generator in the same change so the
   system stays coherent.
5. Generate and inspect a report when possible to verify the shared style.

## Token Rules

- Name tokens by role, not by raw appearance: use names such as
  `--surface`, `--text-muted`, or `--risk-warning`, not `--gray-2` or
  `--blue-card`.
- Maintain a small surface scale for page, primary surface, elevated surface,
  borders, and subdued borders.
- Keep text roles distinct: primary, muted, and dim.
- Treat bull, bear, and neutral as semantic states. Do not reuse them for
  unrelated decoration.
- Use the accent color sparingly for interactive or intentionally emphasized
  content. It must not compete with market-state colors.
- Define hover, focus, selected, loading, empty, and error states when adding
  an interactive component.

## Component Standards

- Asset cards must have consistent padding, border, radius, label/value
  hierarchy, and state treatment.
- Status badges must include readable text in addition to a color treatment.
- Tables must prioritize numeric alignment, concise headers, and readable
  density. Preserve an intentional narrow-screen behavior.
- Charts must use the semantic state palette consistently and retain readable
  labels against the dark background.
- Section headers should make report structure scannable without consuming
  excessive vertical space.
- Reuse spacing and radius values rather than creating one-off values for each
  component.

## Accessibility And Quality

- Keep text and non-text contrast appropriate for a dark interface.
- Do not rely on color alone to express bullish, bearish, warning, or neutral
  states.
- Preserve visible focus indicators.
- Avoid excessive animation; respect a reduced-motion preference if motion is
  added.
- Avoid visual changes that make a time-sensitive financial report slower to
  scan or harder to print/share.

## Boundaries

- Do not make a wholesale visual redesign when the request is only to
  standardize tokens or a component.
- Do not introduce a component library, external font dependency, or build
  tooling without explicit approval.
- Do not alter generated output files directly. Make shared style changes in
  `modules/delivery.py`.

## Completion Report

List added or changed tokens/components, their intended semantic roles, and
where they are used. Note verification performed.
