/* Exercise the photo menu's keyboard, dismissal, and native bridge boundary. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const events={},windowEvents={},errors=[],reveals=[];let current={id:'a'.repeat(32)},focused=null;
class Element{
  constructor(id=''){this.id=id;this.children=[];this.style={};this.attrs={};this.events={};this.isConnected=true;}
  append(child){this.children.push(child);child.parent=this;}
  remove(){this.isConnected=false;if(this.parent)this.parent.children=this.parent.children.filter(item=>item!==this);}
  setAttribute(key,value){this.attrs[key]=value;}
  addEventListener(key,callback){this.events[key]=callback;}
  contains(target){return target===this||this.children.some(child=>child.contains(target));}
  focus(){focused=this;}
  closest(selector){return selector==='.lightbox'?dialog:null;}
  querySelector(selector){return selector==='button'?this.children[0]:null;}
  getBoundingClientRect(){return this.id==='lightbox-image'?{left:50,top:40,width:600,height:400}:{width:230,height:48};}
}
const dialog=new Element(),image=new Element('lightbox-image');
const document={createElement:()=>new Element(),addEventListener:(type,callback)=>(events[type]??=[]).push(callback)};
const window={innerWidth:1000,innerHeight:720,addEventListener:(type,callback)=>(windowEvents[type]??=[]).push(callback),pywebview:{api:{reveal_photo:async id=>{reveals.push(id);return {ok:true};}}}};
vm.runInNewContext(fs.readFileSync('ui/photo-menu.js','utf8'),{window,document,console});
const api=window.ShiguangPhotoMenu;api.configure({currentPhoto:()=>current,icon:()=>'<svg></svg>',toast:(...args)=>errors.push(args)});
const emit=(type,extra={})=>{const event={target:image,clientX:990,clientY:710,preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;},...extra};for(const callback of events[type]||[])callback(event);return event;};
const flush=async()=>{for(let i=0;i<8;i++)await Promise.resolve();};
async function testDecodedPhotoLoading(){
  const source=fs.readFileSync('ui/app.js','utf8'),images=[];
  class Image{constructor(){images.push(this);this.naturalWidth=100;this.decode=()=>new Promise(resolve=>this.finishDecode=resolve);}}
  const loader={};vm.runInNewContext(source.slice(source.indexOf('  const lightboxImages='),source.indexOf('  function renderLightbox()'))+'\nloader.prepare=prepareLightboxImage;loader.cache=lightboxImages;',{Image,loader,imageUrl:photo=>photo.url});
  const photo={url:'/original/a.jpg',thumbnail_url:'/preview/a.jpg'},pending=loader.prepare(photo);
  assert.equal(loader.prepare(photo),pending,'adjacent preloads share one image request');
  let ready=false;pending.then(()=>ready=true);images[0].onload();await flush();assert.equal(ready,false,'loading alone cannot replace the visible photo before decode');
  images[0].finishDecode();await pending;assert.equal(ready,true);
  const fallback=loader.prepare({url:'/broken.jpg',thumbnail_url:'/preview.jpg'});images.at(-1).onerror();await flush();assert.equal(images.at(-1).src,'/preview.jpg');images.at(-1).onload();await flush();images.at(-1).finishDecode();assert.equal((await fallback).src,'/preview.jpg');
  const broken={url:'/missing.jpg',thumbnail_url:'/missing-preview.jpg'},failure=loader.prepare(broken);images.at(-1).onerror();await flush();images.at(-1).onerror();await assert.rejects(failure,/暂时无法显示/);const retry=loader.prepare(broken);assert.notEqual(retry,failure,'failed requests are retryable');images.at(-1).onerror();await flush();images.at(-1).onerror();await assert.rejects(retry);
  for(let i=0;i<8;i++){const item=loader.prepare({url:`/${i}.jpg`});images.at(-1).onload();await flush();images.at(-1).finishDecode();await item;}
  assert(loader.cache.size<=3,'decoded image memory stays bounded');
}
(async()=>{
  await testDecodedPhotoLoading();
  const unrelated=emit('contextmenu',{target:new Element('other')});assert(!unrelated.prevented);assert.equal(dialog.children.length,0);
  assert(emit('contextmenu').prevented);let menu=dialog.children[0];assert.equal(menu.attrs.role,'menu');assert.equal(menu.style.left,'762px');assert.equal(menu.style.top,'664px');assert.equal(focused,menu.children[0]);assert(menu.children[0].innerHTML.includes('在文件管理器中显示'));
  const escape=emit('keydown',{target:focused,key:'Escape'});assert(escape.prevented&&escape.stopped,'Escape closes only the menu, not the viewer');assert.equal(dialog.children.length,0);assert.equal(focused,image);
  emit('contextmenu');emit('pointerdown',{target:new Element('outside')});assert.equal(dialog.children.length,0);
  emit('keydown',{key:'F10',shiftKey:true});menu=dialog.children[0];assert(menu,'keyboard opens the same menu');emit('keydown',{target:focused,key:'ArrowLeft'});assert.equal(dialog.children[0],menu,'menu arrow keys cannot navigate the underlying photo');
  menu.children[0].events.click();await flush();assert.deepEqual(reveals,[current.id]);assert.equal(dialog.children.length,0);assert.equal(errors.length,0);
  let finish;window.pywebview.api.reveal_photo=id=>{reveals.push(id);return new Promise(resolve=>finish=resolve);};emit('contextmenu');dialog.children[0].children[0].events.click();emit('contextmenu');assert.equal(dialog.children.length,0,'a native action cannot be queued repeatedly while busy');finish({ok:false,error:'原图文件不存在。'});await flush();assert.equal(errors.at(-1)[0],'原图文件不存在。');
  emit('contextmenu');api.close(false);assert.equal(dialog.children.length,0,'navigation and modal close remove the popup');
  window.pywebview=null;emit('contextmenu');dialog.children[0].children[0].events.click();await flush();assert(errors.at(-1)[0].includes('桌面相册'));
  emit('contextmenu');windowEvents.resize[0]();assert.equal(dialog.children.length,0);emit('contextmenu');events.scroll[0]();assert.equal(dialog.children.length,0);
  current=null;emit('contextmenu');assert.equal(dialog.children.length,0,'a removed/closed photo cannot expose a stale location action');
  console.log('PASS: photo context menu; viewport positioning; keyboard/focus and dismissal; ID-only native action; busy guard; missing file/bridge errors; lifecycle cleanup.');
})().catch(error=>{console.error(error);process.exitCode=1;});
