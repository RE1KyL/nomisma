import type { Workbook } from '@shared/types/research';

export type GridRow = Record<string, string | number | boolean | null>;
type Sheet = Workbook['sheets'][number];
const rowCache = new WeakMap<Sheet, GridRow[]>();
const EMPTY_ROWS: GridRow[] = [];

/** File-tree refreshes must not look like external workbook edits to RDG. */
export function sheetGridRows(sheet: Sheet | undefined): GridRow[] {
  if (!sheet) return EMPTY_ROWS;
  let rows = rowCache.get(sheet);
  if (!rows) {
    rows = sheet.rows.map((cells, index) => Object.fromEntries([
      ['row', index + 1], ...cells.map(cell => [cell.cell.replace(/[0-9]/g, ''), cell.value]),
    ]));
    rowCache.set(sheet, rows);
  }
  return rows;
}
