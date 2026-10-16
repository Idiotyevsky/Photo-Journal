const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const plays=[],audios=[];
class Audio {constructor(){this.dataset={};this.events={};this.paused=true;this.error=null;}setAttribute(){}addEventListener(n,f){this.events[n]=f;}pause(){this.paused=true;}play(){this.paused=false;return new Promise((resolve,reject)=>plays.push({resolve,reject}));}}
const document={querySelectorAll:()=>[],dispatchEvent:()=>{},body:{append:audio=>audios.push(audio)},createElement:()=>new Audio()};
const window={events:{},addEventListener(n,f){this.events[n]=f;}};
const tracks=[{id:'hymn',title:'Hymn',attribution:"'Hymn' by Scott Buckley",artist:'Scott Buckley',url:'/audio/hymn.mp3',origin:'builtin',enabled:false},{id:'local_a',title:'A',url:'/media/music/local_a.wav',origin:'imported',enabled:true},{id:'local_b',title:'B',url:'/media/music/local_b.flac',origin:'imported',enabled:true}];
vm.runInNewContext(fs.readFileSync('ui/music.js','utf8'),{window,document,CustomEvent:class {},Math:Object.assign(Object.create(Math),{random:()=>0}),fetch:async()=>({ok:true,json:async()=>({tracks})}),Promise,console});
(async()=>{
 const music=window.ShiguangMusic;await music.load();assert.equal(audios.length,0,'startup must remain silent');music.setVolume(.35);music.handleToggle();assert.equal(music.current().id,'local_a');assert.equal(audios[0].volume,.35);music.stop();assert.equal(audios[0].muted,true);music.handleToggle();assert.equal(music.current().id,'local_b','random must avoid last track');plays[0].reject(new Error('late failure'));await Promise.resolve();await Promise.resolve();assert.equal(music.current().playing,true,'stale play rejection must not stop newer selection');
 music.previewTrack('hymn');assert.equal(music.current().id,'hymn','disabled music may be auditioned');assert.equal(music.current().preview,true);audios[0].events.ended();assert.equal(music.current().playing,false,'audition must stop after one track');music.handleToggle();assert.notEqual(music.current().id,'hymn');music.setLibrary([]);assert.equal(music.current().playing,false,'removing currently playing music must stop it');
 music.setLibrary(tracks);music.setVolume(2);music.previewTrack('local_a');assert.equal(audios[0].volume,1);window.events.pagehide();assert.equal(audios[0].paused,true);music.setLibrary([{...tracks[0],title:'用户重命名'}]);assert.ok(music.renderCredits().includes('Hymn'),'source attribution keeps original title');
 console.log('PASS: silent startup; enabled-only random; no immediate repeats; stale playback isolation; disabled-track audition; audition end; library removal; volume; cleanup; original attribution.');
})().catch(error=>{console.error(error);process.exit(1)});
/* Online selection and native-library import share the settings lifecycle. */
(async()=>{
  const listeners={},elements=new Map(),requests=[],players=[],toasts=[];let busy=false,reloads=0,draws=0,reply=null,localPresent=true;
  const modal={type:'settings',tab:'audio'};const app={modal};
  const token='a'.repeat(32),song={token,title:'海边 <晨光>',artist:'摄影配乐',album:'旅途'};
  const events=(type,target)=>{const event={target,preventDefault(){this.prevented=true;}};for(const callback of listeners[type]||[])callback(event);return event;};
  const flush=async()=>{for(let i=0;i<12;i++)await Promise.resolve();};
  const document={getElementById:id=>elements.get(id)||null,addEventListener:(type,callback)=>(listeners[type]??=[]).push(callback),body:{append:item=>players.push(item)},createElement:()=>{const callbacks={};return {src:'',paused:true,removeAttribute(){this.src='';},load(){},remove(){this.removed=true;},pause(){this.paused=true;},play:async function(){this.paused=false;},addEventListener:(name,fn)=>callbacks[name]=fn,callbacks};}};
  const onlineWindow={ShiguangMusic:{stop(){}}};
  vm.runInNewContext(fs.readFileSync('ui/online-music.js','utf8'),{window:onlineWindow,document,AbortController,console});
  const online=onlineWindow.ShiguangOnlineMusic;
  online.configure({getState:()=>app,isBusy:()=>busy,hasTrack:()=>localPresent,volume:()=>.35,redraw(){draws++;},setImporting(value){busy=value;modal.busy=value;},imported:async()=>{reloads++;},toast:(...args)=>toasts.push(args),api:async(path,options)=>{requests.push({path,options});if(reply)return reply(path,options);if(path.endsWith('/search'))return {tracks:[song],page:1,has_more:true};if(path.endsWith('/preview'))return {url:'https://music.126.net/audio.mp3'};return {track:{id:'local'},duplicate:false};}});
  await online.handleAction('settings-online-toggle',{dataset:{}});assert(online.render().includes('GD音乐台'));assert.equal(requests.length,0,'opening online import never searches automatically');
  elements.set('online-music-query',{value:'海边',focus(){}});elements.set('online-music-source',{value:'netease'});
  events('submit',{id:'online-music-search'});await flush();assert(online.render().includes('海边 &lt;晨光&gt;'));assert(!online.render().includes('海边 <晨光>'));
  assert.deepEqual(JSON.parse(requests[0].options.body),{query:'海边',source:'netease',page:1});
  await online.handleAction('settings-online-preview',{dataset:{token}});assert.equal(players.length,1);assert.equal(players[0].volume,.35);assert.equal(players[0].paused,false);
  online.volumeChanged(.2);assert.equal(players[0].volume,.2);await online.handleAction('settings-online-preview',{dataset:{token}});assert(players[0].paused&&players[0].removed);
  let resolvePreview;reply=async path=>path.endsWith('/preview')?new Promise(resolve=>resolvePreview=resolve):{};
  const waiting=online.handleAction('settings-online-preview',{dataset:{token}});await flush();await online.handleAction('settings-online-preview',{dataset:{token}});resolvePreview({url:'https://music.126.net/late.mp3'});await waiting;assert.equal(players.length,1,'a late preview cannot restart after Stop');
  let resolveImport;reply=()=>new Promise(resolve=>resolveImport=resolve);
  const importing=online.handleAction('settings-online-import',{dataset:{token}});await flush();assert(busy&&modal.busy);assert(online.render().includes('导入中…'));const count=requests.length;await online.handleAction('settings-online-import',{dataset:{token}});assert.equal(requests.length,count,'busy import cannot queue duplicates');
  resolveImport({track:{id:'local'},duplicate:false});await importing;assert.equal(reloads,1);assert.equal(busy,false);assert(online.render().includes('已导入'));assert(toasts.at(-1)[0].includes('本地曲库'));localPresent=false;assert(!online.render().includes('已导入'),'removed music can be imported again');localPresent=true;
  const stopped=draws;online.onClose();assert.equal(online.render(),'');assert(draws===stopped,'closing cleans up without reopening the modal');
  reply=null;await online.handleAction('settings-online-toggle',{dataset:{}});let finishSearch;reply=()=>new Promise(resolve=>finishSearch=resolve);
  events('submit',{id:'online-music-search'});await flush();const signal=requests.at(-1).options.signal;online.onClose();app.modal=null;assert(signal.aborted);finishSearch({tracks:[song]});await flush();assert.equal(online.render(),'');
  console.log('PASS: GD search and escaped results; explicit networking; preview/volume/stop and late-response guard; import busy state and local-library refresh; close/abort cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1;});
