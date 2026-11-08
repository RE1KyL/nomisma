import fs from 'fs';
import os from 'os';
import path from 'path';
import {expect,it,vi} from 'vitest';
vi.mock('../data-dir',()=>({getDataDir:()=>'/unused'}));
import {researchPath} from '../research-path';
it('rejects traversal, absolute paths and outside junctions',()=>{
 const base=fs.mkdtempSync(path.join(os.tmpdir(),'nomisma-files-'));
 try{
  const root=path.join(base,'project'),outside=path.join(base,'outside');
  fs.mkdirSync(root);fs.mkdirSync(outside);fs.writeFileSync(path.join(root,'file.md'),'test');
  expect(researchPath(root,'file.md')).toBe(path.join(root,'file.md'));
  for(const value of ['../outside','C:/Windows','a/../../outside'])expect(()=>researchPath(root,value)).toThrow();
  fs.symlinkSync(outside,path.join(root,'link'),'junction');
  expect(()=>researchPath(root,'link')).toThrow();
 }finally{if(path.dirname(path.resolve(base))!==path.resolve(os.tmpdir()))throw new Error('Unexpected temporary target');fs.rmSync(base,{recursive:true});}
});
