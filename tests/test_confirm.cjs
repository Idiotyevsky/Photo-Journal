/* Destructive UI actions must leave their drafts/data intact until confirmation. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
async function confirmation(){
  const listeners={};let root;
  const document={activeElement:null,getElementById:id=>nodes[id],addEventListener:(name,fn)=>listeners[name]=fn,removeEventListener:name=>delete listeners[name],body:{append(node){root=node;}},createElement(){
    const fields={h2:{},p:{}};for(const choice of ['cancel','accept'])fields[`[data-confirm="${choice}"]`]={dataset:{confirm:choice},focus(){document.activeElement=this;}};
    return {querySelector:selector=>fields[selector],querySelectorAll:()=>[fields['[data-confirm="cancel"]'],fields['[data-confirm="accept"]']],addEventListener:(name,fn)=>fields[name]=fn,remove(){this.removed=true;},fields};
  }};
  const nodes={app:{inert:true},'modal-root':{inert:false}},previous={isConnected:true,focus(){document.activeElement=this;}};document.activeElement=previous;
  const window={};vm.runInNewContext(fs.readFileSync('ui/confirm.js','utf8'),{window,document,console});
  const api=window.ShiguangConfirm,waiting=api.ask({title:'<script>',message:'不删除源文件',label:'移除'});
  assert(api.isOpen());assert(nodes.app.inert&&nodes['modal-root'].inert);assert.equal(root.fields.h2.textContent,'<script>');assert.equal(document.activeElement.dataset.confirm,'cancel');assert.equal(await api.ask(),false,'double-click cannot open a second prompt');
  const event={key:'Escape',preventDefault(){},stopImmediatePropagation(){}};listeners.keydown(event);assert.equal(await waiting,false);assert(root.removed);assert(nodes.app.inert);assert(!nodes['modal-root'].inert);assert.equal(document.activeElement,previous);
  const accepted=api.ask();listeners.keydown({...event,key:'Tab'});assert.equal(document.activeElement.dataset.confirm,'accept');root.fields.click({target:{closest:()=>({dataset:{confirm:'accept'}})}});assert.equal(await accepted,true);assert(!api.isOpen());assert(!listeners.keydown);
}
async function settings(){
  const events={},calls=[],toasts=[];let decision=false,reloads=0,clears=0,reply=null;
  const field={value:'',focus(){}},status={},button={disabled:true},state={modal:{type:'settings',tab:'data'}},document={getElementById:id=>id==='initialize-confirmation'?field:id==='initialize-status'?status:null,querySelector:()=>button,querySelectorAll:()=>[],addEventListener:(name,fn)=>events[name]=fn};
  const window={location:{reload(){reloads++;}},ShiguangConfirm:{ask:async()=>decision},ShiguangMusic:{stop(){},current:()=>({preview:false})}};
  vm.runInNewContext(fs.readFileSync('ui/settings.js','utf8'),{window,document,localStorage:{clear(){clears++;}},sessionStorage:{clear(){clears++;}},console});
  const settings=window.ShiguangSettings;
  settings.configure({getState:()=>state,renderModal(){},modalFrame:(_title,_description,body)=>body,icon:()=>'',toast:(...value)=>toasts.push(value),api:async(path,options)=>{calls.push({path,options});return reply?reply():{initialized:true};}});
  assert(settings.renderModal(state.modal).includes('数据管理'));assert(settings.renderModal(state.modal).includes('保留程序、主题、内置地图'));
  const action=()=>settings.handleAction('settings-initialize',{dataset:{}});
  await action();assert.equal(calls.length,0);
  field.value='清空相册';events.input({target:{id:'initialize-confirmation',value:field.value}});assert(!button.disabled);
  await action();assert.equal(calls.length,0,'cancel leaves all data unchanged');assert(!state.modal.busy);
  decision=true;let resolve;reply=()=>new Promise(done=>resolve=done);const first=action();for(let i=0;i<10;i++)await Promise.resolve();assert(state.modal.busy);assert(settings.isImporting());await action();assert.equal(calls.length,1);
  assert.equal(calls[0].path,'/api/initialize');assert.deepEqual(JSON.parse(calls[0].options.body),{confirmation:'清空相册'});resolve({initialized:true});await first;assert.equal(reloads,1);assert.equal(clears,2);
  reply=()=>{throw new Error('请先完成视频导出');};await action();assert.equal(reloads,1);assert.equal(clears,2);assert(!state.modal.busy);assert(status.textContent.includes('视频导出'));assert(toasts.length);
}
(async()=>{await confirmation();await settings();console.log('PASS: confirm/cancel/Escape/Tab/default focus; preserved editor inertness; no stacked prompts; typed initialization guard; cancellation; busy/single-flight guard; reload only after success; recoverable failure.');})().catch(error=>{console.error(error);process.exitCode=1;});
