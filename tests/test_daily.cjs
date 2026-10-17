/* Civil-day selection, automatic opening and failure/clock/focus behaviour. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const builtinQuotes=JSON.parse(fs.readFileSync('ui/daily-quotes.json','utf8')),source=fs.readFileSync('ui/daily.js','utf8');
function setup({modal=false}={}){
  let elapsed=0,current=new Date(2026,9,17,9),removed=false;
  const timers=[],events={},notes=[];
  class Clock extends Date {constructor(...args){super(...(args.length?args:[current.getTime()]));}}
  const classes={values:new Set(['daily-opening']),add(value){this.values.add(value);},remove(value){this.values.delete(value);}};
  const quote={},date={},app={inert:false},root={innerHTML:'',classList:{add(value){this.value=value;}},remove(){removed=true;}};
  const nodes={'daily-opening':root,'app':app,'daily-opening-quote':quote,'daily-opening-date':date};
  const document={body:{classList:classes},hidden:false,getElementById:id=>removed&&id.startsWith('daily-opening')?null:nodes[id]||null,querySelectorAll:()=>notes,addEventListener:(name,fn)=>events[name]=fn,createElement:()=>({set innerHTML(value){this.firstElementChild={innerHTML:value};}})};
  const window={ShiguangDailyQuotes:Object.freeze([...builtinQuotes]),addEventListener:(name,fn)=>events[name]=fn};
  const context={window,document,Date:Clock,performance:{now:()=>elapsed},setTimeout(fn,delay){const timer={fn,at:elapsed+delay,active:true};timers.push(timer);return timer;},clearTimeout(timer){if(timer)timer.active=false;},setInterval(fn){events.tick=fn;},console};
  vm.runInNewContext(source,context);
  const api=window.ShiguangDaily;
  function advance(ms){const end=elapsed+ms;while(true){const timer=timers.filter(item=>item.active&&item.at<=end).sort((a,b)=>a.at-b.at)[0];if(!timer)break;timer.active=false;elapsed=timer.at;timer.fn();}elapsed=end;}
  return {api,quotes:window.ShiguangDailyQuotes,app,root,date,quote,events,classes,notes,advance,get removed(){return removed;},setDate(value){current=value;},start(){api.configure({brandMark:()=>'<svg>existing logo</svg>',hasModal:()=>modal});}};
}
const context=setup();
assert.equal(context.quotes.length,100);assert.equal(new Set(context.quotes).size,100);assert(Object.isFrozen(context.quotes));
const today=context.api.forDate(new Date(2026,9,17,0,1)),evening=context.api.forDate(new Date(2026,9,17,23,59)),tomorrow=context.api.forDate(new Date(2026,9,18,0,1));
assert.equal(today.key,'2026-10-17');assert.equal(today.weekday,'星期六');assert.equal(today.quote,evening.quote);assert.notEqual(today.quote,tomorrow.quote);
const unique=new Set();for(let day=1;day<=100;day++)unique.add(context.api.forDate(new Date(2026,0,day)).quote);assert.equal(unique.size,100);
assert.equal(context.api.forDate(new Date(2024,1,29)).key,'2024-02-29');assert.equal(context.api.forDate(new Date(2026,11,32)).key,'2027-01-01');
context.start();assert(context.app.inert);assert(context.root.innerHTML.includes('existing logo'));assert(!context.root.innerHTML.includes('<button'));assert.equal(context.quote.textContent,today.quote);assert.equal(context.date.dateTime,today.key);
context.advance(500);context.api.ready();context.advance(2099);assert(context.classes.values.has('daily-opening'));context.advance(1);assert(!context.classes.values.has('daily-opening'));assert.equal(context.app.inert,false);context.advance(380);assert(context.removed);
context.api.ready();context.start();assert(context.removed,'later data refreshes cannot replay the splash');
assert(context.api.renderCorner().includes(today.quote));assert(context.api.renderCorner().includes('2026.10.17'));
context.notes.push({innerHTML:''});context.setDate(new Date(2026,9,18,0,0));context.events.tick();assert(context.notes[0].innerHTML.includes(tomorrow.quote));assert(context.notes[0].innerHTML.includes('2026.10.18'));assert(context.removed,'midnight updates the corner without reopening');
context.setDate(new Date(2026,9,19,8));context.events.focus();assert(context.notes[0].innerHTML.includes('2026.10.19'));
const slow=setup();slow.start();slow.advance(6499);assert(slow.app.inert);slow.advance(1);assert.equal(slow.app.inert,false);slow.advance(380);assert(slow.removed,'a stalled request cannot hold the album behind the opening');
const dialog=setup({modal:true});dialog.start();dialog.api.ready();dialog.advance(3000);assert(dialog.app.inert,'finishing the opening must preserve an already-open dialog focus boundary');
const clean=setup();clean.start();clean.advance(1400);clean.api.ready();clean.advance(1200);assert.equal(clean.app.inert,false,'readiness keeps the original minimum deadline');
const managed=setup();managed.start();managed.api.setLines([{text:'自己的开场白 <晨光>'}]);assert.equal(managed.api.forDate().quote,'自己的开场白 <晨光>');assert(managed.api.renderCorner().includes('&lt;晨光&gt;'));managed.api.setLines([]);assert.equal(managed.api.forDate().quote,'');assert(!managed.api.renderCorner().includes('<p'),'an emptied library stays empty instead of reviving deleted builtins');
console.log('PASS: 100 distinct offline reflections; local dates/leap/year boundaries; deterministic daily rotation; automatic opening with no button; minimum and maximum deadlines; preserved modal inertness; midnight/background refresh; no replay.');

(async()=>{
  const events={},requests=[],applied=[],toasts=[];let reply=null,draws=0;
  const state={modal:{type:'settings',tab:'opening'}};
  const document={getElementById:()=>null,querySelector:()=>null,addEventListener:(name,fn)=>events[name]=fn};
  const window={ShiguangDaily:{setLines:lines=>applied.push(lines)}};
  vm.runInNewContext(fs.readFileSync('ui/opening-manager.js','utf8'),{window,document,console});
  const manager=window.ShiguangOpeningManager;
  let lines=[{id:'builtin_001',text:'先看见生活。',origin:'builtin'}];
  manager.configure({getState:()=>state,isBusy:()=>Boolean(state.modal?.busy),icon:()=>'<svg></svg>',redraw:()=>draws++,toast:(...value)=>toasts.push(value),api:async(path,options={})=>{requests.push({path,options});if(reply)return reply(path,options);return {lines:JSON.parse(JSON.stringify(lines))};}});
  const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
  const input=(id,value)=>events.input({target:{id,value}});
  const submit=()=>events.submit({target:{id:'opening-add-form'},preventDefault(){}});
  await manager.load();assert(manager.render().includes('先看见生活。'));assert.equal(applied.at(-1).length,1);
  input('opening-draft','自己的句子 <script>');let resolve;
  reply=()=>new Promise(done=>resolve=done);submit();await flush();assert(state.modal.busy);const count=requests.length;submit();await manager.handleAction('settings-opening-remove',{dataset:{id:'builtin_001'}});assert.equal(requests.length,count,'saving cannot queue another mutation');
  lines.push({id:'custom_a',text:'自己的句子 <script>',origin:'custom'});resolve({lines});await flush();assert.equal(state.modal.busy,false);assert(manager.render().includes('&lt;script&gt;'));assert(!manager.render().includes('>自己的句子 <script>'));assert(manager.render().includes('自己的句子'));assert(manager.render().includes('id="opening-draft" maxlength="120" rows="2" placeholder="让喜欢的句子，陪你翻开相册。"></textarea>'));
  input('opening-search','自己的');assert(!manager.render().includes('先看见生活。'));input('opening-search','');
  const before=requests.length;await manager.handleAction('settings-opening-remove',{dataset:{id:'builtin_001'}});assert(manager.render().includes('删除这句开场白？'));await manager.handleAction('settings-opening-keep',{dataset:{}});assert.equal(requests.length,before);
  reply=async()=>{throw new Error('临时无法保存');};input('opening-draft','保留这份草稿');submit();await flush();assert(manager.render().includes('保留这份草稿'));assert.equal(state.modal.busy,false);
  reply=async()=>({lines:[]});const beforeConfirm=requests.length;await manager.handleAction('settings-opening-delete',{dataset:{id:'builtin_001'}});assert.equal(requests.length,beforeConfirm,'a canceled confirmation cannot be replayed');await manager.handleAction('settings-opening-remove',{dataset:{id:'builtin_001'}});await manager.handleAction('settings-opening-delete',{dataset:{id:'builtin_001'}});assert.equal(requests.at(-1).options.method,'DELETE');assert(manager.render().includes('还没有开场白'));assert.equal(applied.at(-1).length,0);
  const pending=[];reply=()=>new Promise(done=>pending.push(done));const first=manager.load(),second=manager.load();pending[1]({lines:[{id:'custom_new',text:'较新的库',origin:'custom'}]});await second;pending[0]({lines:[{id:'custom_old',text:'较旧的库',origin:'custom'}]});await first;assert(manager.render().includes('较新的库'));assert(!manager.render().includes('较旧的库'));
  assert(draws>0&&toasts.length>=2);
  console.log('PASS: opening manager list/search/escaping; add/delete/cancel; busy guard; saved library refresh; failed writes retain draft; empty catalog and late-load protection.');
})().catch(error=>{console.error(error);process.exitCode=1;});
