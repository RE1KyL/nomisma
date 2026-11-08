import { renderToStaticMarkup } from 'react-dom/server';
import { expect, it } from 'vitest';
import { NomismaEmptyState } from './NomismaEmptyState';
it('uses the supplied Nomisma mark and requested research subtitle',()=>{
  const html=renderToStaticMarkup(<NomismaEmptyState/>);
  expect(html).toContain('brand/nomisma-symbol.png');
  expect(html).toContain('From Data to Discovery');
  expect(html).not.toContain('claude');expect(html).not.toContain('Send a message');
});
