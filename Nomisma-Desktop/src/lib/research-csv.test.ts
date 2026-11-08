import {expect,it} from 'vitest';
import {parseResearchCsv} from './research-csv';
it('preserves quoted commas, escaped quotes and multiline fields',()=>{
 expect(parseResearchCsv('name,value\r\n"a,b","two\nlines"\r\n"a""b",3')).toEqual([['name','value'],['a,b','two\nlines'],['a"b','3']]);
 expect(()=>parseResearchCsv('"unfinished')).toThrow();
});
