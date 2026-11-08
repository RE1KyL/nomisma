/** RFC-style quoted fields, escaped quotes and embedded line breaks. */
export function parseResearchCsv(text: string, delimiter = ','): string[][] {
  const rows: string[][]=[]; let row:string[]=[];let value='';let quoted=false;
  for(let i=0;i<text.length;i++){
    const ch=text[i];
    if(ch==='"') {if(quoted&&text[i+1]==='"'){value+='"';i++;}else quoted=!quoted;}
    else if(ch===delimiter&&!quoted){row.push(value);value='';}
    else if((ch==='\n'||ch==='\r')&&!quoted){if(ch==='\r'&&text[i+1]==='\n')i++;row.push(value);rows.push(row);row=[];value='';}
    else value+=ch;
  }
  if(quoted)throw new Error('CSV has an unterminated quoted field');
  if(value||row.length){row.push(value);rows.push(row);}
  return rows;
}
