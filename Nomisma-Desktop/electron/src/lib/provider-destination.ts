import dns from 'node:dns/promises';
import net from 'node:net';
import https from 'node:https';
import {cloudAllowed,isLoopback} from './privacy-policy';

const blocked=new net.BlockList();
const blocked6=new net.BlockList();for(const [ip,bits] of [['2001::',23],['2001:db8::',32],['2002::',16],['3fff::',20]] as const)blocked6.addSubnet(ip,bits,'ipv6');
for(const [ip,bits] of [['0.0.0.0',8],['10.0.0.0',8],['100.64.0.0',10],['127.0.0.0',8],['169.254.0.0',16],['172.16.0.0',12],['192.0.0.0',24],['192.0.2.0',24],['192.168.0.0',16],['198.18.0.0',15],['198.51.100.0',24],['203.0.113.0',24],['224.0.0.0',4],['240.0.0.0',4]] as const)blocked.addSubnet(ip,bits,'ipv4');
export function publicAddress(address:string):boolean {
 if(net.isIP(address)===4)return !blocked.check(address,'ipv4');
 if(net.isIP(address)!==6)return false;
 // Only global unicast; exclude documentation, Teredo and mapped IPv4.
 return /^[23][0-9a-f]{3}:/i.test(address)&&!blocked6.check(address,'ipv6');
}
export type ProviderTransport = (url:URL,init:RequestInit)=>Promise<Response>;

/** All remote model providers use the same pinned TLS boundary. */
export const pinnedProviderResponse:ProviderTransport = async (url,init) => {
 if(isLoopback(url)){if(url.hostname==='localhost')url.hostname='127.0.0.1';return fetch(url,{...init,redirect:'error'});}
 if(url.protocol!=='https:')throw new Error('Invalid provider destination');
 const host=url.hostname.replace(/^\[|\]$/g,'');
 const addresses=net.isIP(host)?[{address:host,family:net.isIP(host)}]:await dns.lookup(host,{all:true});
 if(!addresses.length||addresses.some(a=>!publicAddress(a.address)))throw new Error('Private provider destination blocked');
 if(!cloudAllowed(url.href)||init.signal?.aborted)throw new Error('Provider connection no longer permitted');
 const target=addresses[0];
 return new Promise((resolve,reject)=>{
  const req=https.request(url,{method:init.method,headers:Object.fromEntries(new Headers(init.headers)),
   agent:false,family:target.family,rejectUnauthorized:true,
   lookup:(_hostname,_options,done)=>done(null,target.address,target.family),signal:init.signal??undefined},incoming=>{
    const status=incoming.statusCode??500;
    if(status>=300&&status<400){incoming.destroy();reject(new Error('Provider redirects blocked'));return;}
    try{
     const headers=new Headers();
     for(const [key,value] of Object.entries(incoming.headers)){
      if(value!==undefined)headers.set(key,Array.isArray(value)?value.join(','):value);
     }
     if(status===204||status===205){incoming.resume();resolve(new Response(null,{status,headers}));return;}
     const body=new ReadableStream<Uint8Array>({start(controller){incoming.on('data',(chunk:Buffer)=>{controller.enqueue(new Uint8Array(chunk));if((controller.desiredSize??0)<=0)incoming.pause();});incoming.on('end',()=>controller.close());incoming.on('error',()=>controller.error(new Error('Provider body failed')));},pull(){incoming.resume();},cancel(){incoming.destroy();}});
     resolve(new Response(body,{status,headers}));
    }catch(error){incoming.destroy();reject(error);}
   });
  req.on('error',reject);if(typeof init.body==='string')req.write(init.body);req.end();
 });
};
