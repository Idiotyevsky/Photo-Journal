/* Exercise the actual SVG layout with a small, deterministic SVG DOM. */
const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const near=(actual,expected,message)=>assert.ok(Math.abs(actual-expected)<1e-5,`${message}: ${actual} != ${expected}`);
const decode=value=>value.replace(/&quot;/g,'"').replace(/&#39;/g,"'").replace(/&lt;/g,'<').replace(/&gt;/g,'>').replace(/&amp;/g,'&');
const identity=()=>({a:1,b:0,c:0,d:1,e:0,f:0});
function multiply(left,right){return {a:left.a*right.a+left.c*right.b,b:left.b*right.a+left.d*right.b,c:left.a*right.c+left.c*right.d,d:left.b*right.c+left.d*right.d,e:left.a*right.e+left.c*right.f+left.e,f:left.b*right.e+left.d*right.f+left.f};}
function matrix(value){return {...value,inverse(){const determinant=this.a*this.d-this.b*this.c;return matrix({a:this.d/determinant,b:-this.b/determinant,c:-this.c/determinant,d:this.a/determinant,e:(this.c*this.f-this.d*this.e)/determinant,f:(this.b*this.e-this.a*this.f)/determinant});}};}
function transform(value=''){
  let result=identity();
  for(const [,name,args]of value.matchAll(/(translate|scale|rotate|matrix)\(([^)]*)\)/g)){
    const numbers=args.split(/[ ,]+/).filter(Boolean).map(Number);let next=identity();
    if(name==='translate'){next.e=numbers[0]||0;next.f=numbers[1]||0;}
    if(name==='scale'){next.a=numbers[0];next.d=numbers[1]??numbers[0];}
    if(name==='rotate'){const angle=numbers[0]*Math.PI/180;next={a:Math.cos(angle),b:Math.sin(angle),c:-Math.sin(angle),d:Math.cos(angle),e:0,f:0};}
    if(name==='matrix')[next.a,next.b,next.c,next.d,next.e,next.f]=numbers;
    result=multiply(result,next);
  }
  return result;
}
function selectorMatch(element,selector){
  selector=selector.trim().replace(/^:scope\s*>?\s*/,'');
  if(!selector)return true;
  const not=[...selector.matchAll(/:not\(([^)]*)\)/g)];selector=selector.replace(/:not\([^)]*\)/g,'');
  if(not.some(([,part])=>selectorMatch(element,part)))return false;
  const attributes=[...selector.matchAll(/\[([^\]=~^$*|\s]+)(?:([~^$*|]?=)["']?([^\]"']*)["']?)?\]/g)];
  selector=selector.replace(/\[[^\]]*\]/g,'');
  for(const [,name,operator,value]of attributes){const actual=element.getAttribute(name);if(actual===null)return false;if(operator==='='&&actual!==value)return false;if(operator==='^='&&!actual.startsWith(value))return false;}
  const tag=selector.match(/^[\w*-]+/)?.[0];if(tag&&tag!=='*'&&element.tagName.toLowerCase()!==tag.toLowerCase())return false;
  const id=selector.match(/#([\w-]+)/)?.[1];if(id&&element.id!==id)return false;
  for(const [,name]of selector.matchAll(/\.([\w-]+)/g))if(!element.classList.contains(name))return false;
  return true;
}
class Node{
  constructor(tag,attributes={}){this.tagName=tag;this.attributes={...attributes};this.children=[];this.parentElement=null;this.style={setProperty(name,value){this[name]=value;}};this.dataset=new Proxy({},{get:(_,key)=>this.getAttribute('data-'+String(key).replace(/[A-Z]/g,char=>'-'+char.toLowerCase())),set:(_,key,value)=>{this.setAttribute('data-'+String(key).replace(/[A-Z]/g,char=>'-'+char.toLowerCase()),value);return true;}});this.classList={contains:name=>(this.attributes.class||'').split(/\s+/).includes(name),add:(...names)=>{this.attributes.class=[...new Set([...(this.attributes.class||'').split(/\s+/).filter(Boolean),...names])].join(' ');},remove:(...names)=>{this.attributes.class=(this.attributes.class||'').split(/\s+/).filter(name=>!names.includes(name)).join(' ');},toggle:(name,on)=>{if(on===undefined)on=!this.classList.contains(name);on?this.classList.add(name):this.classList.remove(name);return on;}};}
  get id(){return this.attributes.id||'';}get parentNode(){return this.parentElement;}get ownerSVGElement(){let node=this.parentElement;while(node&&node.tagName!=='svg')node=node.parentElement;return node;}
  get isConnected(){let node=this;while(node.parentElement)node=node.parentElement;return node===this.document?.root;}
  get clientWidth(){return this.getBoundingClientRect().width;}get clientHeight(){return this.getBoundingClientRect().height;}
  getAttribute(name){return this.attributes[name]??null;}setAttribute(name,value){this.attributes[name]=String(value);}removeAttribute(name){delete this.attributes[name];}
  append(...nodes){for(const node of nodes){node.remove();node.parentElement=this;node.document=this.document;this.children.push(node);node.assignDocument?.(this.document);}}
  appendChild(node){this.append(node);return node;}replaceChildren(...nodes){for(const node of this.children)node.parentElement=null;this.children=[];this.append(...nodes);}
  assignDocument(document){this.document=document;for(const child of this.children)child.assignDocument(document);}
  remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(child=>child!==this);this.parentElement=null;}
  contains(node){return node===this||this.children.some(child=>child.contains(node));}
  matches(selector){return selector.split(',').some(part=>selectorMatch(this,part));}
  closest(selector){const parts=selector.trim().split(/\s+(?![^\[]*\])/);let node=this;while(node){if(selectorMatch(node,parts.at(-1))){let ancestor=node.parentElement,index=parts.length-2;while(index>=0&&ancestor){if(selectorMatch(ancestor,parts[index]))index--;ancestor=ancestor.parentElement;}if(index<0)return node;}node=node.parentElement;}return null;}
  querySelectorAll(selector){const result=[];const visit=node=>{for(const child of node.children){if(child.matches(selector))result.push(child);visit(child);}};visit(this);return result;}
  querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
  get innerHTML(){return this._html||'';}set innerHTML(markup){this._html=String(markup);this.replaceChildren(...parse(this._html));}
  get textContent(){return this._text||this.children.map(child=>child.textContent).join('');}set textContent(value){this._text=String(value);}
  createSVGPoint(){return {x:0,y:0,matrixTransform(value){return {x:this.x*value.a+this.y*value.c+value.e,y:this.x*value.b+this.y*value.d+value.f};}};}
  getScreenCTM(){
    if(this.tagName==='svg'){
      const [x,y,width,height]=(this.getAttribute('viewBox')||'0 0 1000 500').split(/[ ,]+/).map(Number),rect=this.getBoundingClientRect(),scale=Math.min(rect.width/width,rect.height/height);
      return matrix({a:scale,b:0,c:0,d:scale,e:rect.left+(rect.width-width*scale)/2-x*scale,f:rect.top+(rect.height-height*scale)/2-y*scale});
    }
    return matrix(multiply(this.parentElement?.getScreenCTM()||identity(),transform(this.getAttribute('transform')||'')));
  }
  getBoundingClientRect(){if(this.tagName==='svg')return this._rect||{x:100,y:60,left:100,top:60,width:800,height:400,right:900,bottom:460};const box=this.getBBox(),m=this.getScreenCTM();return {left:box.x*m.a+box.y*m.c+m.e,top:box.x*m.b+box.y*m.d+m.f,width:Math.abs(box.width*m.a),height:Math.abs(box.height*m.d)};}
  getBBox(){const value=name=>Number(this.getAttribute(name)||0);if(this.tagName==='circle'){const r=value('r');return {x:value('cx')-r,y:value('cy')-r,width:r*2,height:r*2};}if(this.tagName==='rect')return {x:value('x'),y:value('y'),width:value('width'),height:value('height')};if(this.tagName==='text'){const size=value('font-size')||14;return {x:value('x'),y:value('y')-size,width:[...this.textContent].length*size,height:size};}return {x:0,y:0,width:10,height:8};}
  getComputedTextLength(){return this.getBBox().width;}
  setPointerCapture(id){this.capture=id;}hasPointerCapture(id){return this.capture===id;}releasePointerCapture(){this.capture=null;}focus(){this.document.activeElement=this;}scrollIntoView(){}
}
function parse(markup){
  const root=new Node('fragment'),stack=[root];
  for(const token of markup.match(/<[^>]*>|[^<]+/g)||[]){
    if(token.startsWith('</')){if(stack.length>1)stack.pop();continue;}
    if(token.startsWith('<!'))continue;
    if(!token.startsWith('<')){stack.at(-1)._text=(stack.at(-1)._text||'')+decode(token);continue;}
    const tag=token.match(/^<([\w:-]+)/)?.[1];if(!tag)continue;const attributes={};for(const [,name,,value]of token.matchAll(/([\w:-]+)\s*=\s*(["'])(.*?)\2/g))attributes[name]=decode(value);
    const element=new Node(tag,attributes);stack.at(-1).append(element);
    if(!token.endsWith('/>')&&!['input','img','br','hr','meta','link'].includes(tag))stack.push(element);
  }
  const nodes=[...root.children];for(const node of nodes)node.parentElement=null;return nodes;
}
async function setup(){
  const events={},windowEvents={},observers=[],frames=[],state={modal:null},root=new Node('document');
  const document={root,activeElement:null,addEventListener:(name,fn)=>(events[name]??=[]).push(fn),querySelectorAll:selector=>root.querySelectorAll(selector),querySelector:selector=>root.querySelector(selector),getElementById:id=>root.querySelector('#'+id),createElementNS:(_namespace,tag)=>{const node=new Node(tag);node.document=document;return node;},createElement:tag=>{const node=new Node(tag);node.document=document;return node;},contains:node=>root.contains(node)};root.assignDocument(document);document.documentElement=root;document.body=root;
  const window={innerWidth:1188,addEventListener:(name,fn)=>(windowEvents[name]??=[]).push(fn),getComputedStyle:element=>({fontSize:element.getAttribute('font-size')||'14px',getPropertyValue:name=>element.style[name]||''}),requestAnimationFrame:fn=>{frames.push(fn);return frames.length;},cancelAnimationFrame(){}};
  class ResizeObserver{constructor(callback){this.callback=callback;this.elements=new Set();observers.push(this);}observe(element){this.elements.add(element);}unobserve(element){this.elements.delete(element);}disconnect(){this.elements.clear();}}
  class MutationObserver{constructor(callback){this.callback=callback;}observe(){}disconnect(){}}
  const context=vm.createContext({window,document,console,Node,ResizeObserver,MutationObserver,getComputedStyle:window.getComputedStyle,requestAnimationFrame:window.requestAnimationFrame,cancelAnimationFrame:window.cancelAnimationFrame,setTimeout:fn=>fn(),clearTimeout(){},crypto:require('node:crypto').webcrypto});
  vm.runInContext(fs.readFileSync('ui/atlas-art.js','utf8'),context);
  vm.runInContext(fs.readFileSync('ui/atlas.js','utf8'),context);
  const atlas=window.ShiguangAtlas;
  atlas.configure({getState:()=>state,redraw(){},api:async path=>path==='/api/atlas'?{stops:[],updated_at:null}:JSON.parse(fs.readFileSync('ui'+path,'utf8')),showModal:(type,props)=>state.modal={type,...props},renderModal(){},modalFrame:(_title,_description,body)=>body,closeModal:()=>state.modal=null,toast(){},icon:()=>'',openPlace(){},openCountry(){}});
  await atlas.loadCountries();
  const mount=(markup,width=800,{editing=false,left=100,top=60}={})=>{const wrapper=new Node('div',editing?{id:'atlas-editor-map'}:{});root.append(wrapper);wrapper.innerHTML=markup;const svg=wrapper.querySelector('svg.atlas-svg');assert(svg,'rendered geographic SVG exists');svg._rect={x:left,y:top,left,top,width,height:width/2,right:left+width,bottom:top+width/2};return svg;};
  const emit=(type,target,props={})=>{const event={target,clientX:500,clientY:260,pointerId:1,button:0,detail:1,preventDefault(){this.defaultPrevented=true;},stopImmediatePropagation(){this.stopped=true;},...props};for(const handler of events[type]||[]){handler(event);if(event.stopped)break;}return event;};
  const resize=()=>{for(const observer of observers)observer.callback([...observer.elements].map(target=>({target,contentRect:target.getBoundingClientRect()})));for(const handler of windowEvents.resize||[])handler();let count=0;while(frames.length&&count++<20)frames.shift()();atlas.refreshLayout();};
  return {atlas,state,document,root,mount,emit,resize,observers,window,async action(name,props={}){await atlas.handleAction(name,{dataset:props});}};
}

async function testCountryRoutesAndPersistence(){
const source=fs.readFileSync('ui/atlas-art.js','utf8')+'\n'+fs.readFileSync('ui/atlas.js','utf8');
async function setup(stored={stops:[],updated_at:null}){
 const events={},state={modal:null},posts=[],maps=[],zoomControls=[];
 const document={addEventListener:(name,fn)=>(events[name]??=[]).push(fn),getElementById:()=>null,querySelector:()=>null,querySelectorAll:selector=>selector==='svg.atlas-svg'?maps:selector==='[data-atlas-zoom-controls]'?zoomControls:[]};
 const window={innerWidth:1188,ShiguangConfirm:{ask:async()=>false}};
 vm.runInNewContext(source,{window,document,console,setTimeout:fn=>fn(),crypto:require('node:crypto').webcrypto});
 const atlas=window.ShiguangAtlas;
 atlas.configure({getState:()=>state,redraw:()=>{},api:async(path,options)=>{if(path==='/api/atlas'){if(options){const data=JSON.parse(options.body);posts.push(data);return {...data,updated_at:'2026-10-15'};}return stored;}return JSON.parse(fs.readFileSync('ui'+path,'utf8'));},showModal:(type,props)=>state.modal={type,...props},renderModal:()=>{},modalFrame:(_title,_desc,body)=>body,closeModal:()=>state.modal=null,toast:()=>{},icon:()=>'',openPlace:()=>{},openCountry:()=>{}});
 await atlas.loadCountries();return {atlas,events,state,posts,maps,zoomControls,window};
}
 const s=await setup();
 const photos=[{id:'a',place:'扬州',taken_at:'2025-01-01',country:'CN'},{id:'b',place:'北京',taken_at:'2025-01-02',country:'CN'},{id:'c',place:'巴黎',taken_at:'2025-01-03',country:'FR'},{id:'d',place:'伦敦',taken_at:'2025-01-04',country:'GB'}];
 const pins=html=>(html.match(/class="atlas-pin /g)||[]).length,lines=html=>(html.match(/class="atlas-route-line"/g)||[]).length;
 let html=s.atlas.render(photos);assert.equal(pins(html),2);assert.equal(lines(html),1);assert.ok(html.includes('class="atlas-province"'));assert.ok(!html.includes('<image'));assert.equal(s.atlas.countryForPlace('扬州'),'CN');assert.equal(s.atlas.countryForPlace('巴黎'),'FR');
 await s.atlas.handleAction('atlas-view',{dataset:{scope:'world'}});html=s.atlas.render(photos);assert.equal(pins(html),3);assert.equal(lines(html),2);assert.ok(html.includes('data-atlas-id="country_CN"'));assert.ok(html.includes('世界 · 国家足迹'));
 await s.atlas.handleAction('atlas-select',{dataset:{atlasId:'country_CN'}});html=s.atlas.render(photos);assert.ok(html.includes('2 座城市'));assert.ok(html.includes('扬州 · 北京'));assert.ok(html.includes('atlas-open-country'));
 await s.atlas.handleAction('atlas-edit',{dataset:{}});html=s.atlas.renderModal(s.state.modal);assert.ok(html.includes('>国家</label>'));assert.ok(html.includes('>城市 '));assert.equal(s.state.modal.draft[0].country,'CN');assert.equal(s.state.modal.draft[0].city,'扬州');
 const beforeRemove=s.state.modal.draft.length,firstStop=s.state.modal.draft[0];await s.atlas.handleAction('atlas-remove',{dataset:{atlasIndex:'0'}});assert.equal(s.state.modal.draft.length,beforeRemove,'cancel keeps the route draft');s.window.ShiguangConfirm.ask=async()=>true;await s.atlas.handleAction('atlas-remove',{dataset:{atlasIndex:'1'}});assert.equal(s.state.modal.draft.length,beforeRemove-1);assert.equal(s.state.modal.draft[0],firstStop,'deletion preserves the other route stops');
 const stop=s.state.modal.draft[0];s.events.input[0]({target:{dataset:{atlasField:'city',atlasId:stop.id},value:'北京'}});assert.equal(stop.name,'北京');assert.ok(stop.lat>39&&stop.lat<41);
 s.events.input[0]({target:{dataset:{atlasField:'country',atlasId:stop.id},value:'FR'}});assert.equal(stop.city,'');assert.equal(stop.name,'法国');assert.equal(stop.lat,s.atlas.countries().find(c=>c.code==='FR').lat);
 await s.atlas.handleAction('atlas-save',{dataset:{}});assert.equal(s.posts[0].stops[0].country,'FR');assert.equal(s.posts[0].stops[0].city,'');
 const r=await setup({stops:[{id:'1',name:'扬州',lat:32.39,lon:119.43,country:'',city:''}],updated_at:'saved'});r.atlas.render(photos);await r.atlas.handleAction('atlas-edit',{dataset:{}});assert.equal(r.state.modal.draft[0].city,'扬州');assert.equal(r.state.modal.draft[0].country,'CN');
 const empty=await setup({stops:[],updated_at:'saved'});empty.atlas.render(photos);await empty.atlas.handleAction('atlas-view',{dataset:{scope:'world'}});html=empty.atlas.render(photos);assert.equal(pins(html),3);assert.equal(lines(html),0);
 const countryOnly=await setup();await countryOnly.atlas.handleAction('atlas-view',{dataset:{scope:'world'}});html=countryOnly.atlas.render([{id:'country-cn',country:'CN'},{id:'country-fr',country:'FR'}]);assert.equal(pins(html),2,'photos with a country but no city/GPS must remain separate countries');
}

// Assertions follow the public Atlas contract, with the real artwork loaded above.
(async()=>{
  await testCountryRoutesAndPersistence();
  const context=await setup();assert.equal(typeof context.atlas.refreshLayout,'function','public layout hook is available');
  const photos=[{id:'a',place:'扬州',country:'CN',taken_at:'2025-01-01'},{id:'b',place:'北京',country:'CN',taken_at:'2025-02-01'},{id:'c',place:'巴黎',country:'FR',taken_at:'2025-03-01'},{id:'d',place:'伦敦',country:'GB',taken_at:'2025-04-01'}];
  const initial=context.atlas.render(photos),selectedId=initial.match(/data-atlas-id="([^"]+)"/)[1];
  await context.action('atlas-select',{atlasId:selectedId});
  const markup=context.atlas.render(photos);
  assert(!/<image\b|\.png\b|\.jpe?g\b|data:image/i.test(markup),'geographic base contains no raster image or raster reference');
  assert(markup.includes('class="atlas-country"'));assert(markup.includes('class="atlas-province"'));
  const main=context.mount(markup,800);context.atlas.refreshLayout();context.resize();
  const beforeScroll=main.getAttribute('viewBox');assert.equal(context.emit('wheel',main,{ctrlKey:false,deltaY:-100}).defaultPrevented,undefined,'ordinary wheel keeps page scrolling');assert.equal(main.getAttribute('viewBox'),beforeScroll);
  const countries=main.querySelectorAll('.atlas-country'),provinces=main.querySelectorAll('.atlas-province');assert(countries.length>3);assert(provinces.length>30,'real province geometry is present');
  for(const path of [...countries,...provinces]){assert(path.getAttribute('d').startsWith('M'));assert.equal(path.getAttribute('fill-rule'),'evenodd');assert.equal(path.getAttribute('vector-effect'),'non-scaling-stroke');}
  const lines=main.querySelectorAll('.atlas-route-line'),underlays=main.querySelectorAll('.atlas-route-line-underlay');assert(lines.length);assert.equal(lines.length,underlays.length);
  for(const path of [...lines,...underlays])assert.equal(path.getAttribute('vector-effect'),'non-scaling-stroke');
  assert.equal(lines[0].getAttribute('stroke-width'),'2.2');assert.equal(underlays[0].getAttribute('stroke-width'),'5');
  const selected=main.querySelector(`[data-atlas-pin="${selectedId}"]`);assert(selected?.classList.contains('is-selected'));selected.focus();
  const fixedNodes=['.atlas-pin-hit','.atlas-pin-halo','.atlas-pin-focus','.atlas-pin-dot','.atlas-pin-label-background','.atlas-pin-label'].map(selector=>[selector,selected.querySelector(selector)]);
  for(const [selector,node]of fixedNodes)assert(node,selector+' exists');
  const measurements=()=>{
    const result={};for(const [selector,node]of fixedNodes){const m=node.getScreenCTM(),scale=Math.hypot(m.a,m.b);near(scale,1,selector+' is in CSS pixel coordinates');result[selector]=selector==='.atlas-pin-label'?Number(node.getAttribute('font-size'))*scale:Number(node.getAttribute('r')||node.getAttribute('height'))*scale;if(selector==='.atlas-pin-label-background')result.labelWidth=Number(node.getAttribute('width'))*scale;}
    return result;
  };
  const baseline=measurements();near(baseline['.atlas-pin-hit'],12,'hit target radius');near(baseline['.atlas-pin-halo'],13,'selected halo radius');near(baseline['.atlas-pin-dot'],6,'selected dot radius');near(baseline['.atlas-pin-label'],14,'label font CSS pixels');
  const assertSameSizes=message=>{const current=measurements();for(const key of Object.keys(baseline))near(current[key],baseline[key],message+' '+key);for(const [selector,node]of fixedNodes)assert.equal(selected.querySelector(selector),node,message+' preserves '+selector+' identity');assert.equal(main.querySelector(`[data-atlas-pin="${selectedId}"]`),selected,message+' preserves interactive group');assert.equal(context.document.activeElement,selected,message+' keeps keyboard focus');};
  const assertLabelsInside=svg=>{
    const rect=svg.getBoundingClientRect();for(const pin of svg.querySelectorAll('[data-atlas-pin]')){if(pin.getAttribute('display')==='none'){assert.equal(pin.getAttribute('tabindex'),'-1');assert.equal(pin.getAttribute('aria-hidden'),'true');continue;}const label=pin.querySelector('.atlas-pin-label-background');if(label.getAttribute('visibility')==='hidden')continue;const box=label.getBoundingClientRect();assert(box.left>=rect.left+3.9&&box.top>=rect.top+3.9,'visible labels start inside the current viewport');assert(box.left+box.width<=rect.left+rect.width-3.9&&box.top+box.height<=rect.top+rect.height-3.9,'visible labels end inside the current viewport');}
  };
  assertLabelsInside(main);
  const arrowSizes=svg=>{
    const arrows=svg.querySelectorAll('.atlas-route-arrow');assert(arrows.length,'route direction glyphs are present');
    const labelBoxes=svg.querySelectorAll('.atlas-pin-label-background').filter(label=>label.getAttribute('visibility')!=='hidden'&&label.closest('.atlas-pin').getAttribute('display')!=='none').map(label=>label.getBoundingClientRect());
    for(const arrow of arrows){
      const layers=arrow.querySelectorAll('path'),underlay=arrow.querySelector('.atlas-route-arrow-underlay'),foreground=arrow.querySelector('.atlas-route-arrow-mark');
      assert.equal(layers.length,2,'each direction arrow has a light backing and a dark foreground');assert.equal(layers[0],underlay,'backing is painted before foreground');assert.equal(layers[1],foreground);
      for(const glyph of [underlay,foreground]){
        assert(glyph);const m=glyph.getScreenCTM();near(Math.hypot(m.a,m.b),1,'both arrow layers keep horizontal CSS size');near(Math.hypot(m.c,m.d),1,'both arrow layers keep vertical CSS size');
        assert.equal(glyph.getAttribute('vector-effect'),'non-scaling-stroke');assert.equal(glyph.getAttribute('fill'),'none','arrow is open linework rather than a filled kite');assert.equal(glyph.getAttribute('stroke-linecap'),'round');assert.equal(glyph.getAttribute('stroke-linejoin'),'round');
      }
      assert.equal(underlay.getAttribute('stroke-width'),'6');assert.equal(foreground.getAttribute('stroke-width'),'2.6');
      assert.equal(foreground.getAttribute('d'),'M-7 0H7M1-5.5 7 0 1 5.5','foreground contains a short horizontal shaft and a V-shaped arrowhead');assert.equal(underlay.getAttribute('d'),foreground.getAttribute('d'),'the light backing follows the exact same arrow');assert.notEqual(underlay.getAttribute('stroke'),foreground.getAttribute('stroke'));
      const m=underlay.getScreenCTM(),points=[[-7,0],[7,0],[1,-5.5],[1,5.5]].map(([x,y])=>({x:x*m.a+y*m.c+m.e,y:x*m.b+y*m.d+m.f}));
      const bounds={left:Math.min(...points.map(point=>point.x))-3,right:Math.max(...points.map(point=>point.x))+3,top:Math.min(...points.map(point=>point.y))-3,bottom:Math.max(...points.map(point=>point.y))+3};
      for(const label of labelBoxes)assert(bounds.right<=label.left||bounds.left>=label.left+label.width||bounds.bottom<=label.top||bounds.top>=label.top+label.height,'the complete rotated arrow, including its 6px backing, stays outside visible label boxes');
    }
  };
  arrowSizes(main);
  const selectedAnchor=()=>{const m=selected.getScreenCTM();return {x:m.e,y:m.f};};
  const originalAnchor=selectedAnchor();
  for(let index=0;index<12;index++){
    await context.action('atlas-zoom-in',{atlasView:'china'});
    const anchor=selectedAnchor();near(anchor.x,originalAnchor.x,'button zoom keeps the visible selected pin screen x');near(anchor.y,originalAnchor.y,'button zoom keeps the visible selected pin screen y');
  }
  near(Number(main.getAttribute('viewBox').split(' ')[2]),250,'selected-pin button zoom reaches 4x');
  assert.equal(selected.getAttribute('display'),null,'button zoom keeps the selected pin visible at 4x');
  await context.action('atlas-zoom-reset',{atlasView:'china'});
  const rect=main.getBoundingClientRect(),cursor=main.createSVGPoint();cursor.x=rect.left+rect.width*.25;cursor.y=rect.top+rect.height*.35;
  const pointerAnchor=cursor.matrixTransform(main.getScreenCTM().inverse()),beforeWheel=selectedAnchor();
  context.emit('wheel',main,{ctrlKey:true,deltaY:-120,clientX:cursor.x,clientY:cursor.y});
  const afterPointer=cursor.matrixTransform(main.getScreenCTM().inverse()),afterWheel=selectedAnchor();
  near(afterPointer.x,pointerAnchor.x,'Ctrl-wheel keeps its own pointer longitude despite a selected pin');near(afterPointer.y,pointerAnchor.y,'Ctrl-wheel keeps its own pointer latitude despite a selected pin');
  assert(Math.hypot(afterWheel.x-beforeWheel.x,afterWheel.y-beforeWheel.y)>1,'pointer-anchored wheel takes priority over the selected-pin button anchor');
  await context.action('atlas-zoom-reset',{atlasView:'china'});
  const target=context.atlas.projection('china').point(119.43583,32.39722);
  for(let index=0;index<12;index++){const m=main.getScreenCTM();context.emit('wheel',main,{ctrlKey:true,deltaY:-120,clientX:target.x*m.a+m.e,clientY:target.y*m.d+m.f});}
  const viewBox=main.getAttribute('viewBox');near(Number(viewBox.split(' ')[2]),250,'4x geographic zoom');assertSameSizes('4x zoom');assertLabelsInside(main);arrowSizes(main);
  main._rect={x:100,y:60,left:100,top:60,width:460,height:230,right:560,bottom:290};context.window.innerWidth=760;context.resize();assertSameSizes('narrow resize');assertLabelsInside(main);assert.equal(main.getAttribute('viewBox'),viewBox,'resize preserves geographic viewport');
  main._rect={x:100,y:60,left:100,top:60,width:1100,height:550,right:1200,bottom:610};context.window.innerWidth=1600;context.resize();assertSameSizes('wide resize');assertLabelsInside(main);arrowSizes(main);
  await context.action('atlas-edit');const modal=context.state.modal;assert(modal);modal.selectedId=modal.draft[0].id;modal.picking=true;
  const editor=context.mount(context.atlas.renderModal(modal),620,{editing:true,left:20,top:25});context.resize();
  const editorHit=editor.querySelector('.atlas-pin-hit'),mainHit=main.querySelector('.atlas-pin-hit');near(Math.hypot(editorHit.getScreenCTM().a,editorHit.getScreenCTM().b),1,'editor uses its own 620px CTM');near(Math.hypot(mainHit.getScreenCTM().a,mainHit.getScreenCTM().b),1,'main independently uses its 1100px CTM');
  const stop=modal.draft[0],coordinates={lat:stop.lat,lon:stop.lon},selectedStop=modal.selectedId;
  context.emit('pointerdown',editor,{clientX:310,clientY:170});context.emit('pointermove',editor,{clientX:345,clientY:180});assert(editor.classList.contains('is-dragging'));assert.equal(editor.capture,1);
  editor._rect={x:20,y:25,left:20,top:25,width:800,height:400,right:820,bottom:425};context.resize();
  assert.equal(context.state.modal,modal,'layout leaves modal object untouched');assert.equal(modal.picking,true,'layout preserves coordinate picking');assert.equal(modal.selectedId,selectedStop,'layout preserves selected stop');assert(editor.classList.contains('is-dragging'),'resize leaves active dragging state intact');assert.equal(editor.capture,1,'resize preserves pointer capture');
  context.emit('pointermove',editor,{clientX:365,clientY:180});context.emit('pointerup',editor,{clientX:365,clientY:180});assert.equal(context.emit('click',editor,{clientX:365,clientY:180}).stopped,true,'a relayout during dragging still suppresses the synthetic pick');assert.equal(modal.picking,true);assert.equal(stop.lat,coordinates.lat);assert.equal(stop.lon,coordinates.lon);
  const position=editor.createSVGPoint(),m=editor.getScreenCTM();position.x=target.x*m.a+m.e;position.y=target.y*m.d+m.f;
  context.emit('pointerdown',editor,{clientX:position.x,clientY:position.y});context.emit('pointerup',editor,{clientX:position.x,clientY:position.y});context.emit('click',editor,{clientX:position.x,clientY:position.y});near(stop.lat,32.39722,'picking after vector resize latitude');near(stop.lon,119.43583,'picking after vector resize longitude');assert.equal(modal.picking,false);
  await context.action('atlas-view',{scope:'world'});const worldMarkup=context.atlas.render(photos);assert(!/<image\b|\.png\b/i.test(worldMarkup));const world=context.mount(worldMarkup,700);context.resize();assert(world.querySelectorAll('.atlas-country').length>150,'world uses actual country outlines');assert.equal(world.querySelectorAll('.atlas-province').length,0);arrowSizes(world);
  console.log('PASS: country/city route editing, grouping and persistence; real vector art and countries/provinces; no raster base; non-scaling routes and borders; fixed CSS pin/hit/focus/label/arrow sizes at 1x/4x and narrow/wide resize; selected-pin button anchoring and pointer-wheel priority; independent main/editor CTMs; visible labels bounded; preserved SVG/pin identity, focus, selection, picking and drag capture; accurate post-resize picking.');
})().catch(error=>{console.error(error);process.exitCode=1;});
