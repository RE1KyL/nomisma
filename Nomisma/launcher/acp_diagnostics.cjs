// The installed account provider uses console.info on stdout during discovery.
// Route console diagnostics to stderr before loading the unchanged official CLI.
// JSON-RPC uses process.stdout directly and is never parsed, filtered or altered.
const { Console } = require('node:console');
const diagnostics = new Console({ stdout: process.stderr, stderr: process.stderr });
for (const method of ['log', 'info', 'debug']) console[method] = diagnostics[method].bind(diagnostics);

// Diagnostics may contain tool inputs or provider errors; keep protocol stdout intact.
if(process.env.NOMISMA_PROVIDER_GATEWAY){for(const method of ["log","info","debug","warn","error"])console[method]=()=>{};}

// Managed Harness has one network destination: the authenticated local broker.
// Public data retrieval lives in the separately approved finite Python tools.
if(process.env.NOMISMA_PROVIDER_GATEWAY){
 const gateway=new URL(process.env.NOMISMA_PROVIDER_GATEWAY),net=require('node:net');
 const connect=net.Socket.prototype.connect;
 net.Socket.prototype.connect=function(...args){
  let first=args[0];if(Array.isArray(first))first=first[0];
  const port=typeof first==='object'&&first?first.port:first;
  const host=typeof first==='object'&&first?first.host:(typeof args[1]==='string'?args[1]:'localhost');
  if(Number(port)!==Number(gateway.port)||!['127.0.0.1','localhost','::1'].includes(host??'localhost'))throw new Error('Nomisma runtime network destination denied');
  return connect.apply(this,args);
 };
 const request=globalThis.fetch;
 globalThis.fetch=function(input,options){
  const url=new URL(typeof input==='string'||input instanceof URL?input:input.url);
  if(url.origin!==gateway.origin)throw new Error('Nomisma runtime network destination denied');
  return request(input,{...options,redirect:'error'});
 };
}
