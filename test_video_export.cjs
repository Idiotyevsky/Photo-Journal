const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('ui/video-export.js','utf8');
const TRACK='a'.repeat(32),COLLECTION='b'.repeat(32),JOB='c'.repeat(32);
const deferred=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
const flush=async()=>{for(let index=0;index<6;index++)await Promise.resolve();};
const job=(status,extra={})=>({id:JOB,status,progress:status==='completed'?100:10,message:'正在制作视频',photo_count:3,duration:13.4,video_url:status==='completed'?`/api/video-jobs/${JOB}/file`:null,download_url:status==='completed'?`/api/video-jobs/${JOB}/file?download=1`:null,...extra});
function setup({count=3,tracks=[{id:TRACK,title:'<自己的音乐>',origin:'imported',enabled:false,url:`/media/music/${TRACK}.mp3`},{id:'builtin',title:'内置音乐',origin:'builtin',url:'/audio/builtin.mp3'}]}={}){
  const listeners={},timers=new Map(),elements=new Map(),calls=[],players=[],errors=[],settingsCalls=[];
  const state={collections:[{id:COLLECTION,title:'旅行 <作品>',photo_ids:Array.from({length:count},(_,i)=>String(i))}],photos:Array.from({length:count},(_,i)=>({id:String(i)})),modal:null};
  let html='',renders=0,timerId=0,musicStops=0,request=async(path,options={})=>{
    if(path==='/api/music')return {tracks};
    if(options.method==='POST')return {job:job('preparing')};
    if(options.method==='DELETE')return {job:job('cancelled')};
    return {job:job('encoding',{progress:40})};
  };
  const element=id=>({id,isConnected:true,hidden:false,disabled:false,dataset:{},attributes:{},naturalWidth:960,value:'',textContent:'',_html:'',set innerHTML(value){this._html=value;if(id==='video-export-fields')fillFields();},get innerHTML(){return this._html;},setAttribute(name,value){this.attributes[name]=value;},removeAttribute(name){delete this[name];},pause(){this.paused=true;},load(){this.loads=(this.loads||0)+1;}});
  const put=id=>{const item=element(id);elements.set(id,item);return item;};
  function fillFields(){
    if(!state.modal)return;
    const options=state.modal.options;
    for(const [id,key]of [['video-resolution','resolution'],['video-seconds','seconds_per_photo'],['video-order','order'],['video-music','music_id'],['video-volume','volume']]){
      const item=put(id);item.dataset.videoOption=key;item.value=key==='volume'?String(options[key]*100):String(options[key]??'');
    }
    put('video-volume-value');put('video-listen');
  }
  const render=()=>{
    renders++;html=api.renderModal(state.modal);
    for(const item of elements.values())item.isConnected=false;
    elements.clear();
    for(const match of html.matchAll(/\bid="([^"]+)"/g))put(match[1]);
    const still=elements.get('video-export-still');if(still){still.dataset.videoPreview=state.modal.previewToken;still.src=`/api/collections/${COLLECTION}/video-preview?order=${state.modal.options.order}`;}
    if(html.includes('video-export-preview-status'))put('feedback');
    fillFields();
  };
  const emit=(type,target,extra={})=>{const event={target,preventDefault(){this.defaultPrevented=true;},...extra};for(const listener of listeners[type]||[])listener(event);return event;};
  const document={getElementById:id=>elements.get(id)||null,querySelector:selector=>selector==='.video-export-preview-status'?elements.get('feedback'):null,querySelectorAll:()=>[...elements.values()].filter(item=>item.dataset.videoOption),addEventListener:(type,listener)=>(listeners[type]??=[]).push(listener),body:{append(item){players.push(item);}},createElement:type=>{
    assert.equal(type,'audio');const events={};const player={paused:false,removed:false,src:'',addEventListener(type,listener){events[type]=listener;},pause(){this.paused=true;},removeAttribute(name){delete this[name];},load(){},remove(){this.removed=true;},play:async()=>{},emit(type){events[type]?.();}};return player;
  }};
  const window={ShiguangMusic:{stop(){musicStops++;}},ShiguangSettings:{async handleAction(action,target){settingsCalls.push([action,target.dataset.tab]);}}};
  vm.runInNewContext(source,{window,document,AbortController,setTimeout:handler=>{timers.set(++timerId,handler);return timerId;},clearTimeout:id=>timers.delete(id),console});
  const api=window.ShiguangVideoExport;
  const host={getState:()=>state,api:async(path,options={})=>{calls.push({path,options});return request(path,options);},icon:name=>`<svg data-icon="${name}"></svg>`,toast:(...args)=>errors.push(args),modalFrame:(title,description,body,footer,style)=>`<section class="${style}"><h2>${title}</h2><p>${description}</p>${body}<footer>${footer}</footer></section>`,renderModal:render,showModal(type,data){state.modal={type,...data};render();},closeModal(){api.onClose();state.modal=null;for(const item of elements.values())item.isConnected=false;elements.clear();}};
  api.configure(host);
  return {api,state,host,elements,timers,calls,players,errors,settingsCalls,emit,get html(){return html;},get renders(){return renders;},get musicStops(){return musicStops;},setRequest(fn){request=fn;},async open(){await api.handleAction('video-open',{dataset:{id:COLLECTION}});},ready(){const image=elements.get('video-export-still');assert(image);emit('load',image);return image;},change(id,value,type='change'){const field=elements.get(id);assert(field);field.value=String(value);emit(type,field);},async action(name){await api.handleAction(name,{dataset:{}});},async tick(){assert.equal(timers.size,1);const [id,handler]=timers.entries().next().value;timers.delete(id);handler();await flush();}};
}
(async()=>{
  const context=setup();await context.open();
  assert.equal(context.state.modal.photoCount,3);assert.equal(context.state.modal.options.resolution,'1080p');assert.equal(context.state.modal.options.volume,.5);
  assert.equal(context.state.modal.tracks.length,1,'only imported music is offered, including disabled tracks');
  assert(context.html.includes('旅行 &lt;作品&gt;'));assert(context.elements.get('video-export-fields').innerHTML.includes('&lt;自己的音乐&gt;'));
  assert(context.html.includes('16:9 · 照片居中 · 柔雾玻璃背景 · 淡入淡出'));
  await context.action('video-create');assert.equal(context.calls.filter(call=>call.options.method==='POST').length,0,'no job before real preview is ready');
  const oldPreview=context.ready();assert.equal(context.elements.get('video-create').disabled,false);
  context.change('video-order','newest');assert.equal(context.state.modal.previewStatus,'loading');assert.equal(context.elements.get('video-create').disabled,true);
  context.emit('load',oldPreview);assert.equal(context.state.modal.previewStatus,'loading','old detached preview cannot enable export');
  context.ready();context.change('video-seconds',8);context.change('video-music',TRACK);context.change('video-volume',35,'input');
  assert.equal(context.state.modal.options.volume,.35);assert.equal(context.elements.get('video-export-estimate').textContent,'3 张照片 · 预计 0:22');
  await context.action('video-listen');assert.equal(context.players.length,1);assert.equal(context.players[0].volume,.35);assert.equal(context.players[0].src,`/media/music/${TRACK}.mp3`);assert.equal(context.musicStops,1);
  context.change('video-volume',20,'input');assert.equal(context.players[0].volume,.2);
  context.change('video-music','');assert(context.players[0].paused&&context.players[0].removed,'changing music stops and releases old preview audio');
  context.change('video-music',TRACK);await context.action('video-listen');const playing=context.players.at(-1);
  await context.action('video-create');assert(playing.paused&&playing.removed);assert.equal(context.state.modal.job.status,'preparing');assert.equal(context.timers.size,1);
  const post=context.calls.find(call=>call.options.method==='POST');assert.equal(post.path,`/api/collections/${COLLECTION}/video`);assert.deepEqual(JSON.parse(post.options.body),{resolution:'1080p',seconds_per_photo:8,order:'newest',music_id:TRACK,volume:.2});
  const renders=context.renders;await context.tick();assert.equal(context.state.modal.job.progress,40);assert.equal(context.renders,renders,'poll progress does not regenerate the still or reset controls');assert(context.elements.get('video-export-job-status').innerHTML.includes('40%'));
  context.host.closeModal();assert.equal(context.timers.size,0);assert.equal(context.calls.filter(call=>call.options.method==='DELETE').length,0,'closing the sheet never cancels the export');
  await context.open();assert.equal(context.state.modal.options.seconds_per_photo,8);assert.equal(context.state.modal.job.status,'encoding');assert.equal(context.timers.size,1,'reopening restores the cached job and resumes polling');
  context.setRequest(async(path,options)=>path==='/api/music'?{tracks:[]}:options.method==='DELETE'?{job:job('cancelled')}:{job:job('completed')});
  await context.tick();assert.equal(context.state.modal.job.status,'completed');assert.equal(context.timers.size,0);assert(context.html.includes(`<video id="video-export-result" src="/api/video-jobs/${JOB}/file"`));assert(context.html.includes(`href="/api/video-jobs/${JOB}/file?download=1" download`));
  const video=context.elements.get('video-export-result');context.emit('play',video);assert.equal(context.musicStops,3,'both soundtrack auditions and completed video stop background music');
  context.emit('error',video);assert(context.elements.get('video-export-playback-error').textContent.includes('下载 MP4'));
  await context.action('video-new');assert.equal(context.state.modal.job,null);assert.equal(context.state.modal.previewStatus,'loading');context.emit('error',video);assert.equal(context.state.modal.error,'','late removed video errors cannot contaminate a new draft');
  context.ready();context.setRequest(async(path,options)=>options.method==='POST'?{job:job('encoding')}:options.method==='DELETE'?{job:job('cancelled')}:{tracks:[]});await context.action('video-create');await context.action('video-cancel');assert.equal(context.state.modal.job.status,'cancelled');assert.equal(context.timers.size,0);assert.equal(context.calls.filter(call=>call.options.method==='DELETE').length,1);context.host.closeModal();

  const race=setup();await race.open();race.ready();await race.action('video-create');const pending=deferred();let signal;
  race.setRequest((path,options)=>{signal=options.signal;return pending.promise;});const [timer,callback]=race.timers.entries().next().value;race.timers.delete(timer);callback();assert(signal);race.host.closeModal();assert(signal.aborted,'closing aborts the pending status request');pending.resolve({job:job('completed')});await flush();assert.equal(race.state.modal,null);assert.equal(race.timers.size,0,'late completion cannot restart polling or reopen the sheet');

  const empty=setup({tracks:[]});await empty.open();assert(empty.elements.get('video-export-fields').innerHTML.includes('去设置导入音乐'));empty.ready();await empty.action('video-create');assert.equal(JSON.parse(empty.calls.find(call=>call.options.method==='POST').options.body).music_id,null);empty.host.closeModal();await empty.open();await empty.action('video-cancel');await empty.action('video-import-music');assert.deepEqual(empty.settingsCalls,[['settings-open',undefined],['settings-tab','audio']]);assert.equal(empty.state.modal,null);

  const oversized=setup({count:301});await oversized.open();oversized.ready();assert(oversized.html.includes('一次最多导出 300 张照片'));assert.equal(oversized.elements.get('video-create').disabled,true);await oversized.action('video-create');assert.equal(oversized.calls.filter(call=>call.options.method==='POST').length,0);
  const long=setup({count:300});await long.open();long.ready();long.change('video-seconds',10);assert.equal(long.elements.get('video-create').disabled,true);assert(long.elements.get('video-export-limit').textContent.includes('30 分钟'));long.change('video-seconds',3);assert.equal(long.elements.get('video-create').disabled,false);

  const failed=setup();await failed.open();const bad=failed.elements.get('video-export-still');failed.emit('error',bad);assert.equal(failed.state.modal.previewStatus,'error');assert.equal(failed.elements.get('video-create').disabled,true);await failed.action('video-preview-retry');failed.ready();failed.setRequest(async()=>{throw new Error('<导出失败>');});await failed.action('video-create');assert(failed.html.includes('&lt;导出失败&gt;'));assert.equal(failed.state.modal.busy,false);assert.equal(failed.elements.get('video-create').disabled,false);failed.host.closeModal();
  console.log('PASS: exact video API/options; real preview readiness and stale-event guards; imported-only music/volume/cleanup; progress polling without image reloads; close/reopen and abort races; completion/download; cancellation; no-music import guide; limits and recoverable errors.');
})().catch(error=>{console.error(error);process.exitCode=1;});
