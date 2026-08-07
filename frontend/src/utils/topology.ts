// Wire geometry for the topology diagram, ported from the mockup's `draw()`.
//
// Split out of the component so the maths is testable: the frontend harness is
// `node --test` with no DOM, so anything that reads getBoundingClientRect() cannot be
// covered. What is left in the component is the measuring; everything that decides where a
// curve goes lives here.

export interface Point {
  x: number
  y: number
}

/**
 * Vertical offset of the i-th (1-based) wire arriving at a target, out of `total`.
 *
 * Several routes can land on the same PBS — the whole point of the diagram — and stacked
 * curves would overlap into one thick line. Spreading them symmetrically around the card's
 * mid-height keeps each one readable and makes the fan-in visible at a glance.
 */
export function fanSpread(index: number, total: number): number {
  return (index - (total + 1) / 2) * 14
}

/**
 * The cubic-bezier `d` for one wire.
 *
 * Two cases. Across the columns (PVE -> PBS) the control points sit on the midpoint, giving
 * the flat S-curve of the mockup. Within a column (PBS -> PBS on a sync route) both ends
 * are on the same side, so the curve has to bulge out past the cards — `mx` is placed to
 * the right of both — or it would be drawn straight through them.
 */
export function wirePath(s: Point, t: Point, mx: number): string {
  return `M${s.x} ${s.y} C${mx} ${s.y} ${mx} ${t.y} ${t.x} ${t.y}`
}

/** Control-point x for a cross-column wire: halfway between the two ends. */
export const crossColumnMx = (s: Point, t: Point) => (s.x + t.x) / 2

/** Control-point x for a same-column wire: clear of the rightmost end, so it bows out. */
export const sameColumnMx = (s: Point, t: Point) => Math.max(s.x, t.x) + 62
