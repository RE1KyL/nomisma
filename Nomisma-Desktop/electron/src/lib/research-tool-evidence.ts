/** Decode only real ACP tool fields; never assistant message text. */
export function researchToolName(title:unknown):string|null {
  if(typeof title!=='string')return null;
  const name=title.startsWith('mcp__nomisma__')?title.slice('mcp__nomisma__'.length):title;
  return /^nomisma_[a-z]+(?:_[a-z]+)*$/.test(name)?name:null;
}
export function receiptFromUpdate(update:Record<string,unknown>):Record<string,unknown>|null {
  function parse(value:unknown,depth:number):Record<string,unknown>|null {
    if(depth>7||value==null)return null;
    if(typeof value==='string'){
      if(value.length>50000)return null;
      try{return parse(JSON.parse(value),depth+1);}catch{return null;}
    }
    if(typeof value!=='object')return null;
    const r=value as Record<string,unknown>;
    if(typeof r.ok==='boolean'&&r.data&&Array.isArray(r.artifacts)&&Array.isArray(r.warnings)&&'error'in r)return r;
    for(const key of ['structuredContent','rawOutput','content','text']){
      const v=r[key];if(Array.isArray(v)){for(const item of v){const p=parse(item,depth+1);if(p)return p;}}
      else {const p=parse(v,depth+1);if(p)return p;}
    }return null;
  }
  return parse({rawOutput:update.rawOutput,content:update.content},0);
}
