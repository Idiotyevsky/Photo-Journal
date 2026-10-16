/* Run the real app closure against disposable data and a minimal page shell. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('ui/app.js','utf8');
const startup=/  render\(\);\r?\n  void window\.ShiguangFullscreen\?\.initialize\(\);\r?\n  Promise\.resolve\(window\.ShiguangSettings\?\.initialize\(\)\)\.catch\(\(\)=>\{\}\)\.finally\(\(\)=>refresh\(\)\);/;
assert(startup.test(source),'test seam must match the normal app startup exactly');
const instrumented=source.replace(startup,'  window.__journalTest={state,journalKey,journalRecord,journalGroups,filteredPhotos,renderJournals,renderCollection,render,renderModal,refresh,showModal,closeModal,action,saveJournal};');
const copy=value=>JSON.parse(JSON.stringify(value));
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const decode=value=>value.replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
const attrs=markup=>Object.fromEntries([...markup.matchAll(/([\w-]+)="([^"]*)"/g)].map(([,name,value])=>[name,decode(value)]));
function buttonList(markup){return [...markup.matchAll(/<button\b([^>]*)>([\s\S]*?)<\/button>/g)].map(([,attributes,body])=>({attrs:attrs(attributes),body}));}
function assertNoNestedButtons(markup){let depth=0;for(const [tag]of markup.matchAll(/<\/?button\b[^>]*>/g)){if(tag.startsWith('</'))depth--;else{assert.equal(depth,0,'interactive buttons must not be nested');depth++;}assert(depth>=0);}assert.equal(depth,0);}
function fixture(){return {
  photos:[
    {id:'p1',trip:'江南/旧巷',taken_at:'2025-05-01T12:00:00',title:'柳枝',filename:'one.jpg',place:'扬州',thumbnail_url:'/media/previews/one.jpg'},
    {id:'p2',trip:'江南/旧巷',taken_at:'2025-05-02T12:00:00',title:'小舟',filename:'two.jpg',place:'扬州',thumbnail_url:'/media/previews/two.jpg'},
    {id:'p3',trip:'',taken_at:'2025-06-01T12:00:00',title:'夏日',filename:'three.jpg',place:'广州',thumbnail_url:'/media/previews/three.jpg'},
    {id:'p4',trip:'未写正文',taken_at:'2025-07-01T12:00:00',title:'晴空',filename:'four.jpg',place:'南京',thumbnail_url:'/media/previews/four.jpg'},
  ],
  journals:[{key:'trip:江南/旧巷',title:'江南 <旧巷>',note:'雨声与一段唯一的旧故事'},{key:'trip:独立文字',title:'只有文字的旅途',note:'<script>不能成为标记</script>'}],
  deleted:[],collections:[{id:'portfolio',title:'自己的作品集',description:'',place:'',photo_ids:['p1','p3'],created_at:'2025-01-01',updated_at:'2025-01-01'}],
};}
async function setup(database=fixture()){
  const calls=[],events={},elements=new Map(),timers=[];
  function element(id=''){
    const cache=new Map();return {id,innerHTML:'',inert:false,isConnected:true,disabled:false,dataset:{},classList:{add(){},remove(){}},setAttribute(){},append(){},remove(){this.isConnected=false;},focus(){document.activeElement=this;},getClientRects:()=>[{}],contains:()=>false,querySelector(selector){
      if(cache.has(selector))return cache.get(selector);const action=selector.match(/data-action="([^"]+)"/)?.[1];const button=buttonList(this.innerHTML).find(item=>!action||item.attrs['data-action']===action);if(!button)return null;
      const target=element();target.dataset=Object.fromEntries(Object.entries(button.attrs).filter(([name])=>name.startsWith('data-')).map(([name,value])=>[name.slice(5).replace(/-([a-z])/g,(_,letter)=>letter.toUpperCase()),value]));cache.set(selector,target);return target;
    },querySelectorAll:()=>[]};
  }
  const document={activeElement:null,body:{classList:{add(){},remove(){}}},getElementById:id=>elements.get(id)||null,createElement:()=>element(),addEventListener:(type,listener)=>(events[type]??=[]).push(listener),querySelector:()=>null,querySelectorAll:()=>[]};
  for(const id of ['app','modal-root','toast-root','collection'])elements.set(id,element(id));
  const window={addEventListener(){}};let interceptor=null;
  const fetch=async(path,options={})=>{
    calls.push({path,options});let data;
    if(interceptor){const response=await interceptor(path,options);if(response)return response;}
    if(path==='/api/photos')data={photos:copy(database.photos),storage_bytes:123};
    else if(path==='/api/collections')data={collections:copy(database.collections)};
    else if(path==='/api/journals')data={journals:copy(database.journals),deleted_journal_keys:[...database.deleted]};
    else if(path.startsWith('/api/journals/')){
      const key=decodeURIComponent(path.slice('/api/journals/'.length));
      if(options.method==='DELETE'){database.journals=database.journals.filter(record=>record.key!==key);if(!database.deleted.includes(key))database.deleted.push(key);data={deleted:key};}
      else if(options.method==='POST'){const journal={key,...JSON.parse(options.body),updated_at:'2026-10-16T12:00:00'};database.journals=[...database.journals.filter(record=>record.key!==key),journal];database.deleted=database.deleted.filter(item=>item!==key);data={journal};}
      else throw new Error('Unexpected journal request');
    }else throw new Error('Unexpected URL '+path);
    return {ok:true,status:200,json:async()=>data};
  };
  class FormData{constructor(form){return new Map(Object.entries(form.fields));}}
  vm.runInNewContext(instrumented,{window,document,fetch,FormData,console,URL,Date,setTimeout:fn=>{timers.push(fn);return timers.length;},clearTimeout(){},requestAnimationFrame:fn=>fn(),location:{href:'http://127.0.0.1:9999/?desktop=1'}});
  const app=window.__journalTest;app.state.view='journal';await app.refresh();
  return {app,database,calls,elements,events,setInterceptor:fn=>interceptor=fn,groups:()=>app.journalGroups(app.filteredPhotos()),async action(name,key,target){const button=target||{dataset:{key},disabled:false,isConnected:true};await app.action(name,button);return button;},async openDelete(key){await app.action('delete-journal',{dataset:{key}});assert.equal(app.state.modal.type,'journal-delete');return app.state.modal;},form(fields){const form={fields,reportValidity:()=>true};elements.set('edit-journal-form',form);return form;}};
}
(async()=>{
  const context=await setup(),{app,database}=context,keys=()=>context.groups().map(([key])=>key);
  assert.equal(keys().length,4,'trip photos, month photos, and standalone text each appear once');assert(keys().includes('month:2025-06'));assert(keys().includes('trip:未写正文'));assert(keys().includes('trip:独立文字'));
  const list=app.renderCollection();assertNoNestedButtons(list);assert.equal((list.match(/<article class="journal-card"/g)||[]).length,4);
  const listDeletes=buttonList(list).filter(button=>button.attrs['data-action']==='delete-journal');assert.equal(listDeletes.length,4);
  for(const button of listDeletes){assert(button.attrs.class.includes('icon-button'));assert(button.attrs.class.includes('journal-delete'));assert(button.attrs['aria-label']?.startsWith('删除'));assert(button.attrs.title);assert.equal(button.body.replace(/<svg\b[\s\S]*?<\/svg>/g,'').trim(),'','list trash button is icon-only');}
  assert(!list.includes('<script>'));assert(list.includes('&lt;script&gt;'));
  await context.action('open-journal','trip:江南/旧巷');assert.equal(app.state.scope.key,'trip:江南/旧巷');
  const detail=context.elements.get('app').innerHTML,detailDelete=buttonList(detail).find(button=>button.attrs.class.includes('journal-delete-detail'));assert(detailDelete);assert.equal(detailDelete.attrs['data-key'],'trip:江南/旧巷');assert.equal(detailDelete.body.replace(/<svg\b[\s\S]*?<\/svg>/g,'').trim(),'');assertNoNestedButtons(detail);
  const originalPhotos=JSON.stringify(app.state.photos),originalCollections=JSON.stringify(app.state.collections),originalJournals=JSON.stringify(app.state.journals);
  await context.openDelete('trip:江南/旧巷');assert(context.elements.get('modal-root').innerHTML.includes('照片和作品集会保留'));
  await context.action('close-modal');assert.equal(app.state.modal,null);assert.equal(JSON.stringify(app.state.journals),originalJournals);assert.equal(context.calls.filter(call=>call.options.method==='DELETE').length,0,'cancel does not issue a deletion');
  await context.openDelete('trip:江南/旧巷');context.setInterceptor(async(_path,options)=>options.method==='DELETE'?{ok:false,status:500,json:async()=>({error:'临时失败'})}:null);
  const failedButton={dataset:{},disabled:false,isConnected:true};await assert.rejects(context.action('confirm-delete-journal',null,failedButton),/临时失败/);assert.equal(failedButton.disabled,false);assert.equal(app.state.modal.busy,false);assert.equal(app.state.modal.type,'journal-delete');assert.equal(JSON.stringify(app.state.journals),originalJournals);assert(!app.state.deletedJournalKeys.has('trip:江南/旧巷'));
  context.setInterceptor(null);const pending=deferred();context.setInterceptor(async(_path,options)=>{if(options.method==='DELETE'){await pending.promise;return null;}return null;});
  const before=context.calls.filter(call=>call.options.method==='DELETE').length,submit={dataset:{},disabled:false,isConnected:true},first=context.action('confirm-delete-journal',null,submit);assert.equal(app.state.modal.busy,true);assert.equal(submit.disabled,true);await context.action('confirm-delete-journal',null,submit);assert.equal(context.calls.filter(call=>call.options.method==='DELETE').length,before+1,'busy guard sends exactly one deletion');
  await context.action('close-modal');assert.equal(app.state.modal.type,'journal-delete','confirmation cannot close during an in-flight delete');pending.resolve();await first;context.setInterceptor(null);
  assert.equal(app.state.modal,null);assert.equal(app.state.scope,null);assert.equal(app.state.view,'journal');assert(app.state.deletedJournalKeys.has('trip:江南/旧巷'));assert(!app.state.journals.some(record=>record.key==='trip:江南/旧巷'));assert(!keys().includes('trip:江南/旧巷'));
  assert.equal(JSON.stringify(app.state.photos),originalPhotos,'delete leaves every photo field unchanged');assert.equal(JSON.stringify(app.state.collections),originalCollections,'delete leaves collection memberships unchanged');assert(context.calls.some(call=>call.options.method==='DELETE'&&call.path==='/api/journals/'+encodeURIComponent('trip:江南/旧巷')),'journal keys containing a slash and Chinese are encoded as one complete key');
  app.state.search='唯一的旧故事';assert.equal(app.filteredPhotos().length,0,'removed hand-written text no longer contributes to photo search');assert.equal(context.groups().length,0);app.state.search='';
  const reopened=await setup(database);assert(reopened.app.state.deletedJournalKeys.has('trip:江南/旧巷'));assert(!reopened.groups().some(([key])=>key==='trip:江南/旧巷'),'fresh page state reads persistent deletion markers');
  reopened.app.state.scope={type:'journal',key:'trip:江南/旧巷',title:'过期详情'};reopened.app.state.view='photos';await reopened.app.refresh();assert.equal(reopened.app.state.scope,null,'refresh exits a journal scope that was deleted elsewhere');assert.equal(reopened.app.state.view,'journal');
  database.photos.push({...copy(database.photos[0]),id:'new-import',taken_at:'2026-01-01T12:00:00'});await reopened.app.refresh();assert(!reopened.groups().some(([key])=>key==='trip:江南/旧巷'),'new photos with the same trip do not silently resurrect it');
  for(const key of ['trip:独立文字','trip:未写正文','month:2025-06']){await reopened.openDelete(key);await reopened.action('confirm-delete-journal');assert(reopened.app.state.deletedJournalKeys.has(key));assert(!reopened.groups().some(([item])=>item===key));}
  const empty=reopened.app.renderCollection();assert(empty.includes('这里还没有旅途记忆'));assert(!empty.includes('class="photo-grid'),'deleting all memories cannot fall through into the ordinary photo gallery');assert(reopened.app.state.photos.length>0,'all original photos remain available');
  reopened.app.state.search='晴空';assert.equal(reopened.groups().length,0);assert(reopened.app.renderCollection().includes('没有找到匹配的旅途记忆'));reopened.app.state.search='';
  reopened.app.showModal('journal-edit',{key:'trip:江南/旧巷'});reopened.form({title:'  重新写下的故事  ',note:'新的正文'});
  reopened.setInterceptor(async(_path,options)=>options.method==='POST'?{ok:false,status:500,json:async()=>({error:'保存失败'})}:null);await assert.rejects(reopened.app.saveJournal(),/保存失败/);assert(reopened.app.state.deletedJournalKeys.has('trip:江南/旧巷'),'failed saving cannot clear a deletion marker');assert(database.deleted.includes('trip:江南/旧巷'));reopened.setInterceptor(null);
  await reopened.app.saveJournal();assert(!reopened.app.state.deletedJournalKeys.has('trip:江南/旧巷'),'explicit save clears the local deletion marker');assert(!database.deleted.includes('trip:江南/旧巷'));assert(reopened.groups().some(([key])=>key==='trip:江南/旧巷'));assert.equal(reopened.app.state.journals.find(record=>record.key==='trip:江南/旧巷').title,'重新写下的故事');
  const migrated=await setup(database);assert(migrated.groups().some(([key])=>key==='trip:江南/旧巷'));assert(!migrated.groups().some(([key])=>key==='month:2025-06'),'other deletion markers survive saving a different journal');
  migrated.app.state.photos=[];migrated.app.state.journals=[{key:'trip:纯文字',title:'没有照片的文字',note:'仍可独立删除'}];migrated.app.state.deletedJournalKeys.clear();assert.equal(migrated.groups().length,1);assert(migrated.app.renderCollection().includes('文字手记'));
  await migrated.openDelete('trip:纯文字');await migrated.action('confirm-delete-journal');assert.equal(migrated.groups().length,0);assert(migrated.app.renderCollection().includes('这里还没有旅途记忆'),'deleting the last text-only entry shows the journal empty state even without photos');
  console.log('PASS: real app journal grouping/search and persistent trip/month/text deletions; icon-only sibling delete buttons; no nested buttons; confirmation/cancel/failure/busy guards; encoded keys; unchanged photos/collections; no gallery fallback; explicit save restores only its journal.');
})().catch(error=>{console.error(error);process.exitCode=1;});
