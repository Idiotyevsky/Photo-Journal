const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('ui/collection-order.js','utf8'),appSource=fs.readFileSync('ui/app.js','utf8');
const CID='f'.repeat(32),IDS=['a','b','c'].map(value=>value.repeat(32));
const clone=value=>JSON.parse(JSON.stringify(value));
const pending=()=>{let resolve,reject;const promise=new Promise((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};};
function fixture(){
  const listeners={},cards=new Map(),calls=[],messages=[],window={};let html='',reply=async(_path,options)=>({collection:{...state.collections[0],photo_ids:JSON.parse(options.body).photo_ids}});
  const state={photos:IDS.map(id=>({id,filename:'NEVER_DISPLAY_THIS.JPG',thumbnail_url:`/media/previews/${id}.jpg`})),collections:[{id:CID,title:'旅途 <影像>',photo_ids:[...IDS]}],modal:null};
  const document={addEventListener:(name,listener)=>(listeners[name]??=[]).push(listener),querySelector:()=>null,querySelectorAll:()=>[...cards.values()]};
  vm.runInNewContext(source,{window,document,setTimeout:fn=>fn(),console});const module=window.ShiguangCollectionOrder;
  function render(){html=module.renderModal(state.modal);cards.clear();for(const id of state.modal.photoIds){const values=new Set();cards.set(id,{dataset:{orderPhoto:id},classList:{add:(...names)=>names.forEach(name=>values.add(name)),remove:(...names)=>names.forEach(name=>values.delete(name)),contains:name=>values.has(name)},closest(){return this;}});}}
  const host={getState:()=>state,icon:()=>'<svg></svg>',modalFrame:(title,description,body,footer)=>`<h2>${title}</h2><p>${description}</p>${body}<footer>${footer}</footer>`,showModal(type,data){state.modal={type,...data};render();},renderModal:render,closeModal(){module.onClose();state.modal=null;},storeCollection(value){state.collections=[value];},redraw(){},toast:message=>messages.push(message),api:async(path,options)=>{calls.push({path,options});return reply(path,options);}};
  module.configure(host);
  const action=(name,data={})=>module.handleAction(name,{dataset:data});
  const emit=(name,id,transfer)=>{const event={target:cards.get(id)||{closest:()=>null},dataTransfer:transfer,preventDefault(){this.prevented=true;},stopPropagation(){this.stopped=true;}};for(const handler of listeners[name]||[])handler(event);return event;};
  return {state,calls,messages,module,host,cards,action,emit,setReply:fn=>reply=fn,get html(){return html;},async open(){await action('order-open',{id:CID});}};
}
async function moduleTests(){
  const test=fixture();await test.open();assert(test.html.includes('旅途 &lt;影像&gt;'));assert(!test.html.includes('NEVER_DISPLAY_THIS'));assert(test.html.includes('draggable="true"'));assert(test.html.includes('order-save" disabled'));
  await test.action('order-move',{id:IDS[2],delta:'-1'});assert.deepEqual(clone(test.state.modal.photoIds),[IDS[0],IDS[2],IDS[1]]);assert.deepEqual(clone(test.state.collections[0].photo_ids),IDS,'draft movement does not save early');
  test.host.closeModal();await test.open();assert.deepEqual(clone(test.state.modal.photoIds),IDS,'cancel discards draft');
  const data=new Map(),transfer={setData:(key,value)=>data.set(key,value),getData:key=>data.get(key)||''};
  test.emit('dragstart',IDS[0],transfer);assert.equal(transfer.effectAllowed,'move');
  const over=test.emit('dragover',IDS[2],transfer);assert(over.prevented&&over.stopped);assert(test.cards.get(IDS[2]).classList.contains('is-drop-target'));
  const drop=test.emit('drop',IDS[2],transfer);assert(drop.prevented&&drop.stopped);assert.deepEqual(clone(test.state.modal.photoIds),[IDS[1],IDS[2],IDS[0]]);
  assert.equal(test.calls.length,0);await test.action('order-save');assert.equal(test.calls.length,1);assert.equal(test.calls[0].path,`/api/collections/${CID}/order`);
  assert.deepEqual(JSON.parse(test.calls[0].options.body),{photo_ids:[IDS[1],IDS[2],IDS[0]],expected_photo_ids:IDS});assert.equal(test.state.modal,null);assert.deepEqual(clone(test.state.collections[0].photo_ids),[IDS[1],IDS[2],IDS[0]]);
  await test.open();const before=clone(test.state.modal.photoIds);test.emit('drop',IDS[0],{getData:()=>IDS[2]});assert.deepEqual(clone(test.state.modal.photoIds),before,'external text/file drops cannot reorder');
  test.emit('dragstart',IDS[1],transfer);test.host.closeModal();await test.open();test.emit('drop',IDS[0],transfer);assert.deepEqual(clone(test.state.modal.photoIds),before,'a drag from a closed editor cannot alter a new editor');
  await test.action('order-move',{id:IDS[0],delta:'-1'});const delayed=pending();test.setReply(()=>delayed.promise);const saving=test.action('order-save');assert(test.state.modal.busy);assert(test.html.includes('draggable="false"'));
  const busyOrder=clone(test.state.modal.photoIds);await test.action('order-move',{id:IDS[1],delta:'1'});await test.action('order-save');assert.equal(test.calls.length,2,'double save does not create another request');assert.deepEqual(clone(test.state.modal.photoIds),busyOrder);
  delayed.reject(new Error('作品集已有变化 <重试>'));await saving;assert(!test.state.modal.busy);assert(test.html.includes('作品集已有变化 &lt;重试&gt;'));assert.deepEqual(clone(test.state.collections[0].photo_ids),before,'failed saving leaves stored order unchanged');
  test.setReply(async(_path,options)=>({collection:{...test.state.collections[0],photo_ids:JSON.parse(options.body).photo_ids}}));await test.action('order-save');assert.equal(test.state.modal,null);assert.deepEqual(clone(test.state.collections[0].photo_ids),busyOrder);
}
async function appIntegration(){
  const startup=/  render\(\);\r?\n  void window\.ShiguangFullscreen\?\.initialize\(\);\r?\n  Promise\.resolve\(window\.ShiguangSettings\?\.initialize\(\)\)\.catch\(\(\)=>\{\}\)\.finally\(\(\)=>refresh\(\)\);/;
  assert(startup.test(appSource));const text=appSource.replace(startup,'  window.__orderApp={state,openCollection,filteredPhotos,render,action};');
  const events={},elements=new Map();function element(){return {innerHTML:'',isConnected:true,classList:{add(){},remove(){}},querySelector:()=>null,querySelectorAll:()=>[],append(){},remove(){},focus(){}};}
  for(const id of ['app','modal-root','toast-root','collection'])elements.set(id,element());
  const document={getElementById:id=>elements.get(id),activeElement:null,body:{classList:{add(){},remove(){}}},querySelector:()=>null,querySelectorAll:()=>[],createElement:element,addEventListener:(name,handler)=>(events[name]??=[]).push(handler)};
  const window={addEventListener(){}},context={window,document,console,Date,URL,location:{href:'http://127.0.0.1:12345/'},setTimeout:()=>0,clearTimeout(){},fetch:async(_path,options)=>({ok:true,json:async()=>({collection:{...window.__orderApp.state.collections[0],photo_ids:JSON.parse(options.body).photo_ids}})})};
  vm.createContext(context);vm.runInContext(source,context);vm.runInContext(text,context);const app=window.__orderApp;
  app.state.loading=false;app.state.photos=IDS.map((id,index)=>({id,title:id,filename:id+'.jpg',taken_at:`2025-01-0${3-index}T12:00:00`,thumbnail_url:`/media/previews/${id}.jpg`}));app.state.collections=[{id:CID,title:'旅行',photo_ids:[IDS[1],IDS[0],IDS[2]]}];
  app.openCollection(CID);assert.equal(app.state.sort,'collection');assert.deepEqual(Array.from(app.filteredPhotos(),photo=>photo.id),[IDS[1],IDS[0],IDS[2]]);assert(elements.get('app').innerHTML.includes('编排照片'));assert(elements.get('app').innerHTML.includes('value="collection" selected'));
  app.state.sort='date-desc';assert.deepEqual(Array.from(app.filteredPhotos(),photo=>photo.id),IDS,'date sorting stays available without overwriting the saved sequence');
  await app.action('layout-timeline',{dataset:{}});await app.action('layout-grid',{dataset:{}});assert.equal(app.state.sort,'collection');
  await app.action('order-open',{dataset:{id:CID}});assert.equal(app.state.modal.type,'collection-order');assert(elements.get('modal-root').innerHTML.includes('order-grid'));
  await app.action('order-move',{dataset:{id:IDS[2],delta:'-1'}});await app.action('order-save',{dataset:{}});assert.equal(app.state.modal,null);assert.equal(app.state.sort,'collection');assert.deepEqual(Array.from(app.filteredPhotos(),photo=>photo.id),[IDS[1],IDS[2],IDS[0]]);
  await app.action('nav',{dataset:{view:'photos'}});assert.equal(app.state.sort,'date-desc');assert(!elements.get('app').innerHTML.includes('value="collection"'));
}
(async()=>{await moduleTests();await appIntegration();console.log('PASS: real collection editor drag/drop and accessible moves; draft/cancel/save/retry/busy behavior; no filenames; external/stale drags ignored; real app order, date sorts, timeline return, modal integration and video default.');})().catch(error=>{console.error(error);process.exitCode=1;});
