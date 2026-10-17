import type { Track } from '../domain/contracts';

type Demo = {tracks:(Track & {url:string})[]; items:string[]; plays:number[]; targets:number[][]; weights:number[][]; catalog_key:string};
let data: Promise<Demo> | undefined;
let session: {phase:string; period:number; periods:number; finished:boolean; profile:{items:string[];artists:string[]}; recommendation:unknown} | undefined;
let result: {request_id:string; tracks:Track[]; model_version:string; route:string; latency_ms:number; notice:null} | undefined;
let heard = new Set<string>();
const requests = new Map<string, string[]>();
const events: unknown[] = [];
export async function browserDemo(path:string, method:string, input:unknown): Promise<unknown> {
  const d: Demo = await (data ??= fetch('/demo/demo.json').then(r => {if(!r.ok) throw new Error('デモの準備中です');return r.json();}));
  const body = (input || {}) as Record<string, any>;
  const url = new URL(path, 'https://demo.invalid/');
  const route = url.pathname.replace(/^\/+/, '');
  if(route === 'status') return {mode:'demo', audience:'participant', experience:'normal', ready:true, catalog_key:d.catalog_key, logging_mode:'browser-memory', models:[], unavailable:[]};
  if(route === 'sessions' && method === 'POST') {
    session={phase:'profile',period:1,periods:1,finished:false,profile:{items:[],artists:[]},recommendation:null};
    heard=new Set();requests.clear();events.length=0;result=undefined;return session;
  }
  if(!session) throw new Error('セッションを開始してください');
  if((route === 'session' && method === 'DELETE') || route === 'session/logout') {session=undefined;requests.clear();events.length=0;result=undefined;heard.clear();return {deleted:true};}
  if(route === 'session/state') return session;
  if(route === 'session/export') return {events,requests:[...requests],session};
  if(route === 'catalog') {const offset=Number(url.searchParams.get('offset') || 0);return {tracks:d.tracks.slice(offset,offset+24),total:d.tracks.length,artists:[]};}
  if(session.finished) throw new Error('終了済みです');
  if(route === 'profile') {session.profile={items:body.items,artists:body.artists || []};return {selected_count:body.items.length};}
  if(route === 'session/progress') {session.phase=body.phase;return {accepted:true};}
  if(route === 'session/finish' || route === 'session/leave') {session.finished=true;return {finished:true};}
  if(route === 'recommendations') {
    const start=performance.now();
    const seeds=[...new Set([...session.profile.items,...heard])].sort();
    const familiar=body.goal==='familiar' && heard.size>0;
    const history=familiar ? [...heard].sort() : seeds;
    const scores=Array(d.items.length).fill(0) as number[];
    for(const id of history) {const i=d.items.indexOf(id);if(i<0)continue;d.targets[i].forEach((target,j)=>scores[target]+=d.weights[i][j]);}
    const candidates=d.items.map((id,i)=>({id,i})).filter(({id})=>familiar ? heard.has(id) : !seeds.includes(id));
    candidates.sort((a,b)=>(history.length ? scores[b.i]-scores[a.i] : d.plays[b.i]-d.plays[a.i]) || d.plays[b.i]-d.plays[a.i] || (a.id<b.id?-1:1));
    const tracks=candidates.slice(0,body.top_k || 5).map(({id})=>d.tracks.find(t=>t.item_id===id)!);
    result={request_id:crypto.randomUUID(),tracks,model_version:'demo-itemcf',route:history.length?'itemcf_history':'popularity_fallback',latency_ms:performance.now()-start,notice:null};
    requests.set(result.request_id,tracks.map(t=>t.item_id));session.recommendation=result;return result;
  }
  if(route==='previews' || route==='playbacks') {
    const track=d.tracks.find(t=>t.item_id===body.item_id);
    if(!track || (route==='previews' ? session.phase!=='profile' : !requests.get(body.request_id)?.includes(body.item_id))) throw new Error('試聴できません');
    return {playback_id:crypto.randomUUID(),url:track.url,external:true};
  }
  if(route==='events') {
    if(!requests.has(body.request_id) || (body.item_id && !requests.get(body.request_id)!.includes(body.item_id))) throw new Error('推薦が見つかりません');
    if(body.kind==='play') heard.add(body.item_id);
    events.push(body);return {accepted:true};
  }
  throw new Error('この操作はデモでは利用できません');
}
