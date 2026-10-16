/* Verify the colors host integration using the real app closure, without UI globals. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('ui/app.js','utf8');
const startup=/  render\(\);\r?\n  void window\.ShiguangFullscreen\?\.initialize\(\);\r?\n  Promise\.resolve\(window\.ShiguangSettings\?\.initialize\(\)\)\.catch\(\(\)=>\{\}\)\.finally\(\(\)=>refresh\(\)\);/;
assert(startup.test(source),'the app startup seam must stay explicit and test-only');
const instrumented=source.replace(startup,'  window.__colorsAppTest={state,render,renderCollection,filteredPhotos,action,refresh,updatePhoto,favorite,openPhoto,navigatePhoto,closeModal};');
const copy=value=>JSON.parse(JSON.stringify(value));
const flush=async()=>{for(let count=0;count<8;count++)await Promise.resolve();};
async function setup(){
  const events={},elements=new Map(),requests=[],timers=[];
  const database={photos:[
    {id:'first',title:'赤红 <晨光>',filename:'red.jpg',trip:'旅行',place:'扬州',country:'CN',taken_at:'2025-01-01T10:00:00',thumbnail_url:'/media/previews/first.jpg',url:'/media/originals/first.jpg',favorite:false},
    {id:'second',title:'海边',filename:'blue.jpg',trip:'',place:'巴黎',country:'FR',taken_at:'2025-02-01T10:00:00',thumbnail_url:'/media/previews/second.jpg',url:'/media/originals/second.jpg',favorite:true},
    {id:'third',title:'日落',filename:'sun.jpg',trip:'旅行',place:'扬州',country:'CN',taken_at:'2025-03-01T10:00:00',thumbnail_url:'/media/previews/third.jpg',url:'/media/originals/third.jpg',favorite:false}],journals:[],collections:[]};
  const calls={configure:0,ensure:0,render:0,invalidate:0,click:[],change:[]};let host=null,interceptor=null;
  const colors={configure(value){calls.configure++;host=value;},ensureLoaded(){calls.ensure++;return Promise.resolve();},render(){calls.render++;return `<section class="colors-panel" data-render="${calls.render}">色彩分析内容</section>`;},invalidate(){calls.invalidate++;},async handleClick(target){calls.click.push(target);},async handleChange(target){calls.change.push(target);}};
  function element(id=''){
    return {id,innerHTML:'',inert:false,isConnected:true,disabled:false,dataset:{},classList:{add(){},remove(){}},setAttribute(){},append(){},remove(){this.isConnected=false;},focus(){document.activeElement=this;},getClientRects:()=>[{}],contains:()=>false,insertAdjacentHTML(_where,html){this.innerHTML+=html;},querySelector(selector){
      const action=selector.match(/data-action="([^"]+)"/)?.[1];if(action&&this.innerHTML.includes(`data-action="${action}"`))return element();return null;
    },querySelectorAll:()=>[]};
  }
  const document={activeElement:null,body:{classList:{add(){},remove(){}}},getElementById:id=>elements.get(id)||null,createElement:()=>element(),addEventListener:(type,listener)=>(events[type]??=[]).push(listener),querySelector:()=>null,querySelectorAll:()=>[]};
  for(const id of ['app','modal-root','toast-root','collection'])elements.set(id,element(id));
  const window={ShiguangColors:colors,addEventListener(){}};
  const fetch=async(path,options={})=>{
    requests.push({path,options});if(interceptor){const response=await interceptor(path,options);if(response)return response;}
    let data;
    if(path==='/api/photos')data={photos:copy(database.photos),storage_bytes:123};
    else if(path==='/api/journals')data={journals:copy(database.journals),deleted_journal_keys:[]};
    else if(path==='/api/collections')data={collections:copy(database.collections)};
    else if(path.startsWith('/api/photos/')&&options.method==='POST'){
      const id=decodeURIComponent(path.slice('/api/photos/'.length)),index=database.photos.findIndex(photo=>photo.id===id);assert(index>=0);
      database.photos[index]={...database.photos[index],...JSON.parse(options.body)};data={photo:copy(database.photos[index])};
    }else throw new Error('Unexpected request '+path);
    return {ok:true,json:async()=>data};
  };
  vm.runInNewContext(instrumented,{window,document,fetch,console,URL,Date,setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},location:{href:'http://127.0.0.1:9999/?desktop=1'}});
  const app=window.__colorsAppTest;await app.refresh();
  const emit=async(type,target)=>{const event={target,preventDefault(){this.defaultPrevented=true;}};for(const listener of events[type]||[])listener(event);await flush();return event;};
  return {app,calls,colors,get host(){return host;},elements,requests,database,emit,setInterceptor:fn=>interceptor=fn,async action(name,dataset={}){await app.action(name,{dataset});}};
}
(async()=>{
  const context=await setup(),{app,calls,elements}=context;
  assert.equal(calls.configure,1);assert.equal(context.host.getState(),app.state);
  for(const name of ['api','icon','redraw','openPhoto','openPhotos'])assert.equal(typeof context.host[name],'function',`colors receives host.${name}`);
  assert(elements.get('app').innerHTML.includes('data-view="colors"'));assert(elements.get('app').innerHTML.includes('色彩足迹'));
  await context.action('nav',{view:'colors'});
  assert.equal(app.state.view,'colors');assert.equal(app.state.scope,null);assert(calls.ensure>0);assert(calls.render>0);
  const overview=elements.get('app').innerHTML;
  assert(overview.includes('class="colors-panel"'));assert(!overview.includes('class="stats-row"'),'colors overview has no generic album stats');assert(!overview.includes('class="toolbar"'),'colors overview has no generic search/sort toolbar');assert(!overview.includes('id="photo-search"'));
  const beforeRedraw=calls.render;context.host.redraw();assert.equal(calls.render,beforeRedraw+1);assert(elements.get('collection').innerHTML.includes('class="colors-panel"'));
  await context.action('nav',{view:'photos'});const offscreen=calls.render;context.host.redraw();assert.equal(calls.render,offscreen,'colors background updates do not overwrite another page');
  await context.action('nav',{view:'colors'});
  const clicked={dataset:{action:'color-open-photos',period:'2025-01'},closest(selector){return selector==='[data-action]'?this:null;}};
  const clickEvent=await context.emit('click',clicked);assert.equal(clickEvent.defaultPrevented,true);assert.equal(calls.click.at(-1),clicked,'delegated color click reaches the module unchanged');
  const disabled={...clicked,disabled:true};const clickCount=calls.click.length;await context.emit('click',disabled);assert.equal(calls.click.length,clickCount,'disabled color controls do not trigger actions');
  const period={id:'color-period',value:'quarter',dataset:{}};await context.emit('change',period);assert.equal(calls.change.at(-1),period);
  const scope={id:'another-control',value:'favorites',dataset:{action:'color-scope'}};await context.emit('change',scope);assert.equal(calls.change.at(-1),scope,'module-specific data-action also forwards changes');
  for(const field of ['scope','compare-a','compare-b']){const select={value:field==='scope'?'favorites':'2025-01',dataset:{colorField:field}};await context.emit('change',select);assert.equal(calls.change.at(-1),select,'real select data-color-field forwards without an id or action');}
  const unrelated={id:'unrelated',dataset:{}};const changeCount=calls.change.length;await context.emit('change',unrelated);assert.equal(calls.change.length,changeCount);
  app.state.search='discard me';app.state.layout='timeline';app.state.selectionMode=true;app.state.selectedIds.add('second');
  const ids=['first','third','missing','first'],photosBefore=JSON.stringify(app.state.photos),collectionsBefore=JSON.stringify(app.state.collections),rendersBefore=calls.render;
  context.host.openPhotos({title:'2025 · <赤红>',photoIds:ids});ids.push('second');
  assert.equal(app.state.view,'colors');assert.equal(app.state.scope.type,'color');assert.equal(app.state.scope.title,'2025 · <赤红>');assert.equal(app.state.search,'');assert.equal(app.state.layout,'grid');assert.equal(app.state.selectionMode,false);assert.equal(app.state.selectedIds.size,0);
  assert.equal(calls.render,rendersBefore,'a color scope renders the photo gallery rather than reloading the overview');
  assert.deepEqual(Array.from(app.filteredPhotos(),photo=>photo.id),['third','first'],'color scope filters IDs, ignores unknowns and duplicates, and retains normal date sorting');
  const scoped=elements.get('app').innerHTML;assert(scoped.includes('返回色彩足迹'));assert(scoped.includes('2025 · &lt;赤红&gt;'));assert(!scoped.includes('2025 · <赤红>'));assert(scoped.includes('class="photo-grid'));assert(!scoped.includes('class="colors-panel"'));
  assert.equal(JSON.stringify(app.state.photos),photosBefore);assert.equal(JSON.stringify(app.state.collections),collectionsBefore);
  const scopedRenders=calls.render;context.host.redraw();assert.equal(calls.render,scopedRenders,'analysis redraw does not replace an active color gallery');
  context.host.openPhoto('third');assert.equal(app.state.modal.type,'lightbox');assert.equal(app.state.lightboxId,'third');assert(elements.get('modal-root').innerHTML.includes('1 / 2'));
  app.navigatePhoto(1);assert.equal(app.state.lightboxId,'first');assert(elements.get('modal-root').innerHTML.includes('2 / 2'));assert(!elements.get('modal-root').innerHTML.includes('海边'));
  app.navigatePhoto(1);assert.equal(app.state.lightboxId,'third','lightbox cycles only through the selected palette photos');
  app.closeModal();assert.equal(app.state.scope.type,'color');assert.equal(app.state.view,'colors');assert.equal(app.state.lightboxId,null);
  await context.action('clear-scope');assert.equal(app.state.scope,null);assert.equal(app.state.view,'colors');assert(elements.get('app').innerHTML.includes('class="colors-panel"'));assert(!elements.get('app').innerHTML.includes('id="photo-search"'));
  const beforeFavorite=calls.invalidate;await app.favorite('first');assert.equal(app.state.photos.find(photo=>photo.id==='first').favorite,true);assert.equal(calls.invalidate,beforeFavorite+1,'favorite edits invalidate cached color analysis');
  const beforeDateEdit=calls.invalidate;await app.updatePhoto('first',{taken_at:'2026-04-05T10:00:00'});assert.equal(calls.invalidate,beforeDateEdit+1);assert.equal(app.state.photos.find(photo=>photo.id==='first').taken_at,'2026-04-05T10:00:00');
  const beforeRefresh=calls.invalidate;await app.refresh();assert.equal(calls.invalidate,beforeRefresh+1,'fresh import/delete refresh invalidates module results');
  context.setInterceptor(async(_path,options)=>options.method==='POST'?{ok:false,json:async()=>({error:'暂时无法保存'})}:null);
  const beforeFailure=calls.invalidate,unchanged=JSON.stringify(app.state.photos);await assert.rejects(app.updatePhoto('first',{favorite:false}),/暂时无法保存/);assert.equal(calls.invalidate,beforeFailure);assert.equal(JSON.stringify(app.state.photos),unchanged,'failed metadata edits cannot mutate photo/color state');
  console.log('PASS: real app colors navigation/configuration/render lifecycle; hidden overview stats/search; click/change delegation; immutable palette ID scope and return; scoped lightbox navigation; invalidation after favorite/date/refresh; failed edits preserve state.');
})().catch(error=>{console.error(error);process.exitCode=1;});
