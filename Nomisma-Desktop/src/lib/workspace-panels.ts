export type PanelSide = 'left' | 'right';
export const PANEL_LIMITS = {
  left: { min:120, max:260, initial:150 },
  right: { min:220, max:520, initial:280 },
} as const;

export function boundedPanelWidth(side: PanelSide, requested: number, total: number, other: number): number {
  const limit = PANEL_LIMITS[side];
  const max = Math.max(limit.min, Math.min(limit.max, total - other - 400 - 14));
  return Math.round(Math.max(limit.min, Math.min(max, Number.isFinite(requested) ? requested : limit.initial)));
}

export function storedPanelWidths(value: string | null): {left:number;right:number} {
  try {
    const parsed = JSON.parse(value ?? '{}');
    return {
      left: boundedPanelWidth('left', Number(parsed.left ?? 150), 1400, 280),
      right: boundedPanelWidth('right', Number(parsed.right ?? 280), 1400, 150),
    };
  } catch { return { left:150, right:280 }; }
}
