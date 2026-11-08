import { expect, it } from 'vitest';
import { sheetGridRows } from './workbook-grid';

it('keeps the editing row identity through polling, navigation and resize rerenders', () => {
  const sheet = { name: 'Prices', rows: [[{ cell:'B2', value:504.7, kind:'number', format:'General' }]] };
  const original = sheetGridRows(sheet);
  // RDG closes EDIT if getActiveRow() !== originalRow. Polling cannot change it.
  for (let tick=0; tick<6; tick++) expect(sheetGridRows(sheet)[0]).toBe(original[0]);
  expect(sheetGridRows(sheet)).toBe(original);
  const committed = { ...sheet, rows:[[{ ...sheet.rows[0][0], value:609.25 }]] };
  expect(sheetGridRows(committed)[0].B).toBe(609.25);
  expect(sheetGridRows(committed)[0]).not.toBe(original[0]);
  expect(original[0].B).toBe(504.7);
});
