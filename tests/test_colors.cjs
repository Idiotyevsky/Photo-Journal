/* Exercise the actual colour page, including synchronous host redraw re-entry. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('ui/colors.js','utf8');
const copy=value=>JSON.parse(JSON.stringify(value));
const flush=async()=>{for(let i=0;i<18;i++)await Promise.resolve();};
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
function payload(period='month',scope='all') {
  return {period,scope,total_photos:3,analyzed_photos:3,skipped_photos:0,undated_photos:0,periods:[
    {key:'2026-06',label:'2026年6月',photo_count:2,photo_ids:['a','b'],palette:[{hex:'#2277BB',share:.7,photo_ids:['a']},{hex:'#FF9922',share:.3,photo_ids:['b']}],metrics:{warmth:.3,brightness:.6,saturation:.7},representative_ids:['a','b']},
    {key:'2026-04',label:'2026年4月',photo_count:1,photo_ids:['c'],palette:[{hex:'#BB6633',share:1,photo_ids:['c']}],metrics:{warmth:.7,brightness:.4,saturation:.3},representative_ids:['c']}
  ]};
}
function setup(responder=async path=>{const p=new URL(path,'http://localhost').searchParams;return payload(p.get('period'),p.get('scope'));}) {
  const window={},state={view:'colors',photos:['a','b','c'].map(id=>({id,filename:'PRIVATE_FILE_NAME.jpg',thumbnail_url:`/media/previews/${id}.jpg`}))};
  vm.runInNewContext(source,{window,console});
  const colors=window.ShiguangColors,calls=[],opened=[],photoIds=[];
  let markup='',depth=0,redraws=0;
  colors.configure({getState:()=>state,api:async path=>{calls.push(path);return responder(path);},icon:()=>'',openPhotos:value=>opened.push(copy(value)),openPhoto:id=>photoIds.push(id),redraw:()=>{
    assert(++depth<5,'loading/cached/error render must never recurse without bound');
    redraws++;void colors.ensureLoaded();markup=colors.render();depth--;
  }});
  const click=(action,data={})=>colors.handleClick({dataset:{action,...data}});
  const change=(field,value)=>colors.handleChange({dataset:{colorField:field},value});
  return {colors,state,calls,opened,photoIds,click,change,get markup(){return markup;},get redraws(){return redraws;}};
}
(async()=>{
  const page=setup();await page.colors.ensureLoaded();await flush();
  assert.equal(page.calls.length,1,'host render re-entry reuses the request');
  assert(page.markup.includes('2026年6月'));assert(page.markup.includes('#2277BB'));assert(page.markup.includes('70%'));assert(page.markup.includes('colors-trajectories'));
  assert(!page.markup.includes('PRIVATE_FILE_NAME'));assert(!page.markup.includes('colors-compare-grid'),'comparison starts collapsed');
  await page.colors.ensureLoaded();assert.equal(page.calls.length,1,'revisiting an unchanged filter reuses data');
  await page.click('color-swatch',{periodKey:'2026-06',colorIndex:'0'});assert.deepEqual(page.opened.at(-1),{title:'2026年6月 · #2277BB',photoIds:['a']});
  await page.click('color-open-period',{periodKey:'2026-06'});assert.deepEqual(page.opened.at(-1).photoIds,['a','b']);
  await page.click('color-photo',{periodKey:'2026-06',photoId:'b'});assert.deepEqual(page.photoIds,['b']);
  await page.click('color-photo',{periodKey:'2026-06',photoId:'c'});assert.equal(page.photoIds.length,1,'representatives cannot open a photo from another period');
  await page.click('color-compare');assert(page.markup.includes('colors-compare-grid'));assert(page.markup.includes('更冷'));assert(page.markup.includes('更亮'));
  await page.change('compare-a','2026-06');assert(page.markup.includes('更暖'),'choosing the opposite period swaps the other selection');
  await page.click('color-close-compare');assert(!page.markup.includes('colors-compare-grid'));
  await page.change('scope','favorites');assert(page.calls.at(-1).endsWith('scope=favorites'));
  await page.click('color-period',{period:'quarter'});assert(page.calls.at(-1).includes('period=quarter'));
  await page.click('color-period',{period:'year'});assert(page.calls.at(-1).includes('period=year'));
  await page.click('color-period',{period:'invalid'});assert.equal(page.calls.length,4);
  await page.colors.invalidate();assert.equal(page.calls.length,5,'library edits refresh the active filter');
  page.state.view='photos';const redraws=page.redraws;await page.colors.invalidate();assert.equal(page.redraws,redraws,'offscreen invalidation never repaints another page');assert.equal(page.calls.length,5);
  page.state.view='colors';await page.colors.ensureLoaded();assert.equal(page.calls.length,6);

  let fail=true;
  const failure=setup(async()=>{if(fail)throw new Error('磁盘暂时忙 <重试>');return payload();});
  await failure.colors.ensureLoaded();await flush();
  assert.equal(failure.calls.length,1,'failed render re-entry cannot cause an automatic retry storm');
  assert(failure.markup.includes('磁盘暂时忙 &lt;重试&gt;'));assert(failure.markup.includes('color-retry'));assert(!failure.markup.includes('colors-loading'));
  await failure.colors.ensureLoaded();assert.equal(failure.calls.length,1,'failure remains visible until an explicit retry');
  fail=false;await failure.click('color-retry');await flush();assert.equal(failure.calls.length,2);assert(failure.markup.includes('2026年6月'));assert(!failure.markup.includes('color-retry'));

  const requests=[],racing=setup(path=>{const pending=deferred();requests.push({path,...pending});return pending.promise;});
  const old=racing.colors.ensureLoaded();await flush();
  const newest=racing.change('scope','favorites');await flush();assert.equal(requests.length,2);
  requests[1].resolve({...payload('month','favorites'),total_photos:0,analyzed_photos:0,periods:[]});await newest;await flush();
  assert(racing.markup.includes('把偏爱的颜色'));requests[0].resolve(payload());await old;await flush();assert(racing.markup.includes('把偏爱的颜色'),'late all-photo response cannot overwrite favorites');
  const pending=racing.colors.invalidate();await flush();const next=racing.colors.invalidate();await flush();
  requests[2].resolve(payload('month','favorites'));await pending;await flush();assert(!racing.markup.includes('2026年6月'),'invalidated in-flight response stays discarded');
  requests[3].resolve({...payload('month','favorites'),total_photos:0,analyzed_photos:0,periods:[]});await next;await flush();assert(racing.markup.includes('把偏爱的颜色'));

  const blank=setup(async()=>({...payload(),total_photos:0,analyzed_photos:0,periods:[]}));await blank.colors.ensureLoaded();assert(blank.markup.includes('还没有留下色彩足迹'));assert(blank.markup.includes('data-action="import"'));
  const partial=setup(async()=>({period:'month',scope:'all',total_photos:2,analyzed_photos:0,skipped_photos:2,undated_photos:2,periods:[{key:'undated',label:'未注明日期',photo_count:2,photo_ids:['a','b'],palette:[],metrics:{brightness:null,saturation:null,warmth:null},representative_ids:[]}]}));
  await partial.colors.ensureLoaded();assert(partial.markup.includes('2 张暂未解析'));assert(partial.markup.includes('待分析'));assert(!partial.markup.includes('colors-trajectories'));assert(!partial.markup.includes('NaN'));
  const unsafe=setup(async()=>{const data=payload();data.periods[0].label='<script>bad</script>';data.periods[0].palette.push({hex:'red;background:url(https://outside)',share:.1,photo_ids:['a']});return data;});
  await unsafe.colors.ensureLoaded();assert(unsafe.markup.includes('&lt;script&gt;'));assert(!unsafe.markup.includes('<script>'));assert(!unsafe.markup.includes('https://outside'));
  console.log('PASS: real colors page with re-entrant host rendering; equal-share bands and linked photos; month/quarter/year and favorites filters; comparison swap; cache/invalidation; stale response protection; persistent error and retry; empty/undated/unreadable data; escaped markup and local images.');
})().catch(error=>{console.error(error);process.exitCode=1;});
