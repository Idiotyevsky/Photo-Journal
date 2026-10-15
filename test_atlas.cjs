const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('ui/atlas-art.js','utf8')+'\n'+fs.readFileSync('ui/atlas.js','utf8');
async function setup(stored={stops:[],updated_at:null}){
 const events={},state={modal:null},posts=[],maps=[],zoomControls=[];
 const document={addEventListener:(name,fn)=>(events[name]??=[]).push(fn),getElementById:()=>null,querySelector:()=>null,querySelectorAll:selector=>selector==='svg.atlas-svg'?maps:selector==='[data-atlas-zoom-controls]'?zoomControls:[]};
 const window={innerWidth:1188};
 vm.runInNewContext(source,{window,document,console,setTimeout:fn=>fn(),crypto:require('node:crypto').webcrypto});
 const atlas=window.ShiguangAtlas;
 atlas.configure({getState:()=>state,redraw:()=>{},api:async(path,options)=>{if(path==='/api/atlas'){if(options){const data=JSON.parse(options.body);posts.push(data);return {...data,updated_at:'2026-10-15'};}return stored;}return JSON.parse(fs.readFileSync('ui'+path,'utf8'));},showModal:(type,props)=>state.modal={type,...props},renderModal:()=>{},modalFrame:(_title,_desc,body)=>body,closeModal:()=>state.modal=null,toast:()=>{},icon:()=>'',openPlace:()=>{},openCountry:()=>{}});
 await atlas.loadCountries();return {atlas,events,state,posts,maps,zoomControls};
}
const close=(actual,expected,message)=>assert.ok(Math.abs(actual-expected)<1e-7,`${message}: ${actual} != ${expected}`);
const classList=()=>{const classes=new Set();return {add:name=>classes.add(name),remove:name=>classes.delete(name),toggle:(name,on)=>on?classes.add(name):classes.delete(name),contains:name=>classes.has(name)};};
function createMap(context,view,editing=false,initial='0 0 1000 500'){
 const attributes={viewBox:initial},buttons={out:{disabled:true},in:{disabled:false},reset:{disabled:true}},level={textContent:'100%'};
 const controls={dataset:{atlasView:view},querySelector:selector=>selector==='[data-atlas-zoom-level]'?level:selector.includes('zoom-out')?buttons.out:selector.includes('zoom-in')?buttons.in:buttons.reset};
 const svg={dataset:{atlasView:view},classList:classList(),parentElement:{classList:classList()},setAttribute:(name,value)=>attributes[name]=value,getAttribute:name=>attributes[name],closest:selector=>selector==='svg.atlas-svg'||editing&&selector==='#atlas-editor-map svg.atlas-svg'?svg:null,
  createSVGPoint:()=>({x:0,y:0,matrixTransform(matrix){return {x:this.x*matrix.a+this.y*(matrix.c||0)+matrix.e,y:this.x*(matrix.b||0)+this.y*matrix.d+matrix.f};}}),
  getScreenCTM(){const [x,y,width,height]=attributes.viewBox.split(' ').map(Number),a=800/width,d=400/height,e=100-x*a,f=50-y*d;return {a,d,e,f,inverse:()=>({a:1/a,d:1/d,e:-e/a,f:-f/d})};},
  setPointerCapture(id){this.captured=id;},hasPointerCapture(id){return this.captured===id;},releasePointerCapture(){this.captured=null;}};
 context.maps.push(svg);context.zoomControls.push(controls);
 return {svg,buttons,level,box:()=>attributes.viewBox.split(' ').map(Number)};
}
function emit(context,name,target,properties={}){
 const event={target,clientX:500,clientY:250,pointerId:1,button:0,detail:1,defaultPrevented:false,stopped:false,preventDefault(){this.defaultPrevented=true;},stopImmediatePropagation(){this.stopped=true;},...properties};
 for(const handler of context.events[name]||[]){handler(event);if(event.stopped)break;}
 return event;
}
const mapPoint=(svg,x,y)=>{const point=svg.createSVGPoint();point.x=x;point.y=y;return point.matrixTransform(svg.getScreenCTM().inverse());};
const screenCoordinates=(svg,point)=>{const matrix=svg.getScreenCTM();return {clientX:point.x*matrix.a+matrix.e,clientY:point.y*matrix.d+matrix.f};};
const zoom=(context,view,action)=>context.atlas.handleAction('atlas-zoom-'+action,{dataset:{atlasView:view}});
const svgViewBox=markup=>markup.match(/class="atlas-svg[^\"]*" viewBox="([^\"]+)"/)[1];
(async()=>{
 const s=await setup();
 const photos=[{id:'a',place:'扬州',taken_at:'2025-01-01',country:'CN'},{id:'b',place:'北京',taken_at:'2025-01-02',country:'CN'},{id:'c',place:'巴黎',taken_at:'2025-01-03',country:'FR'},{id:'d',place:'伦敦',taken_at:'2025-01-04',country:'GB'}];
 const pins=html=>(html.match(/class="atlas-pin /g)||[]).length,lines=html=>(html.match(/class="atlas-route-line"/g)||[]).length;
 let html=s.atlas.render(photos);assert.equal(pins(html),2);assert.equal(lines(html),1);assert.ok(html.includes('class="atlas-province"'));assert.ok(!html.includes('<image'));assert.equal(s.atlas.countryForPlace('扬州'),'CN');assert.equal(s.atlas.countryForPlace('巴黎'),'FR');
 await s.atlas.handleAction('atlas-view',{dataset:{scope:'world'}});html=s.atlas.render(photos);assert.equal(pins(html),3);assert.equal(lines(html),2);assert.ok(html.includes('data-atlas-id="country_CN"'));assert.ok(html.includes('世界 · 国家足迹'));
 await s.atlas.handleAction('atlas-select',{dataset:{atlasId:'country_CN'}});html=s.atlas.render(photos);assert.ok(html.includes('2 座城市'));assert.ok(html.includes('扬州 · 北京'));assert.ok(html.includes('atlas-open-country'));
 await s.atlas.handleAction('atlas-edit',{dataset:{}});html=s.atlas.renderModal(s.state.modal);assert.ok(html.includes('>国家</label>'));assert.ok(html.includes('>城市 '));assert.equal(s.state.modal.draft[0].country,'CN');assert.equal(s.state.modal.draft[0].city,'扬州');
 const stop=s.state.modal.draft[0];s.events.input[0]({target:{dataset:{atlasField:'city',atlasId:stop.id},value:'北京'}});assert.equal(stop.name,'北京');assert.ok(stop.lat>39&&stop.lat<41);
 s.events.input[0]({target:{dataset:{atlasField:'country',atlasId:stop.id},value:'FR'}});assert.equal(stop.city,'');assert.equal(stop.name,'法国');assert.equal(stop.lat,s.atlas.countries().find(c=>c.code==='FR').lat);
 await s.atlas.handleAction('atlas-save',{dataset:{}});assert.equal(s.posts[0].stops[0].country,'FR');assert.equal(s.posts[0].stops[0].city,'');
 const r=await setup({stops:[{id:'1',name:'扬州',lat:32.39,lon:119.43,country:'',city:''}],updated_at:'saved'});r.atlas.render(photos);await r.atlas.handleAction('atlas-edit',{dataset:{}});assert.equal(r.state.modal.draft[0].city,'扬州');assert.equal(r.state.modal.draft[0].country,'CN');
 const empty=await setup({stops:[],updated_at:'saved'});empty.atlas.render(photos);await empty.atlas.handleAction('atlas-view',{dataset:{scope:'world'}});html=empty.atlas.render(photos);assert.equal(pins(html),3);assert.equal(lines(html),0);
 const countryOnly=await setup();await countryOnly.atlas.handleAction('atlas-view',{dataset:{scope:'world'}});html=countryOnly.atlas.render([{id:'country-cn',country:'CN'},{id:'country-fr',country:'FR'}]);assert.equal(pins(html),2,'photos with a country but no city/GPS must remain separate countries');
 const z=await setup();z.atlas.render(photos);const china=createMap(z,'china');
 let plain=emit(z,'wheel',china.svg,{ctrlKey:false,deltaY:-100});assert.equal(plain.defaultPrevented,false);assert.deepEqual(china.box(),[0,0,1000,500]);
 await zoom(z,'china','in');assert.deepEqual(china.box(),[100,50,800,400]);assert.equal(china.level.textContent,'125%');assert.equal(china.buttons.out.disabled,false);
 const anchor=mapPoint(china.svg,340,330);const wheel=emit(z,'wheel',china.svg,{ctrlKey:true,deltaY:-100,clientX:340,clientY:330});assert.equal(wheel.defaultPrevented,true);const anchored=mapPoint(china.svg,340,330);close(anchored.x,anchor.x,'wheel keeps cursor longitude');close(anchored.y,anchor.y,'wheel keeps cursor latitude');
 for(let i=0;i<30;i++)await zoom(z,'china','in');assert.equal(china.buttons.in.disabled,true);close(china.box()[2],250,'maximum zoom 4x');
 const start=china.box();emit(z,'pointerdown',china.svg);emit(z,'pointermove',china.svg,{clientX:580,clientY:290});close(china.box()[0],start[0]-25,'drag x uses original CTM');close(china.box()[1],start[1]-12.5,'drag y uses original CTM');assert(china.svg.classList.contains('is-dragging'));
 emit(z,'pointermove',china.svg,{clientX:-100000,clientY:-100000});close(china.box()[0],750,'pan right boundary');close(china.box()[1],375,'pan lower boundary');emit(z,'pointermove',china.svg,{clientX:100000,clientY:100000});close(china.box()[0],0,'pan left boundary');close(china.box()[1],0,'pan upper boundary');emit(z,'pointerup',china.svg);assert.equal(china.svg.classList.contains('is-dragging'),false);assert.equal(emit(z,'click',china.svg).stopped,true,'drag cannot trigger a pin action');
 emit(z,'pointerdown',china.svg);emit(z,'pointerup',china.svg);assert.equal(emit(z,'click',china.svg).stopped,false,'next real click is not suppressed');
 const retained=china.box().join(' ');let chinaMarkup=z.atlas.render(photos);const firstId=chinaMarkup.match(/data-atlas-id="([^"]+)"/)[1];await z.atlas.handleAction('atlas-select',{dataset:{atlasId:firstId}});assert.equal(svgViewBox(z.atlas.render(photos)),retained,'pin selection redraw retains view');
 await z.atlas.handleAction('atlas-view',{dataset:{scope:'world'}});assert.equal(svgViewBox(z.atlas.render(photos)),'0 0 1000 500','world starts at its own 1x view');const world=createMap(z,'world');await zoom(z,'world','in');assert.equal(world.level.textContent,'125%');assert.equal(china.box().join(' '),retained,'world zoom does not change China');await z.atlas.handleAction('atlas-view',{dataset:{scope:'china'}});assert.equal(svgViewBox(z.atlas.render(photos)),retained);
 await z.atlas.handleAction('atlas-edit',{dataset:{}});assert.equal(svgViewBox(z.atlas.renderModal(z.state.modal)),retained,'editor retains the current China view');
 for(let i=0;i<30;i++)await zoom(z,'china','out');assert.deepEqual(china.box(),[0,0,1000,500]);assert.equal(china.buttons.out.disabled,true);assert.equal(china.buttons.reset.disabled,true);assert.equal(china.level.textContent,'100%');await zoom(z,'china','in');await zoom(z,'china','reset');assert.deepEqual(china.box(),[0,0,1000,500]);
 const p=await setup();p.atlas.render(photos);await p.atlas.handleAction('atlas-edit',{dataset:{}});const editor=createMap(p,'china',true),draft=p.state.modal.draft[0],original={lat:draft.lat,lon:draft.lon};p.state.modal.selectedId=draft.id;p.state.modal.picking=true;
 const location=p.atlas.projection('china').point(119.43583,32.39722);for(let i=0;i<6;i++)emit(p,'wheel',editor.svg,{ctrlKey:true,deltaY:-120,...screenCoordinates(editor.svg,location)});
 emit(p,'pointerdown',editor.svg);emit(p,'pointermove',editor.svg,{clientX:460,clientY:230});emit(p,'pointerup',editor.svg,{clientX:460,clientY:230});const dragClick=emit(p,'click',editor.svg,{clientX:460,clientY:230});assert.equal(dragClick.stopped,true);assert.equal(p.state.modal.picking,true);assert.equal(draft.lat,original.lat);assert.equal(draft.lon,original.lon);
 const clickPosition=screenCoordinates(editor.svg,location);emit(p,'pointerdown',editor.svg,clickPosition);emit(p,'pointerup',editor.svg,clickPosition);emit(p,'click',editor.svg,clickPosition);close(draft.lat,32.39722,'zoomed and panned picking latitude');close(draft.lon,119.43583,'zoomed and panned picking longitude');assert.equal(p.state.modal.picking,false);
 console.log('PASS: country/city routes and persistence; 1–4x zoom and controls; pointer-focused Ctrl-wheel; ordinary page scrolling; bounded drag; no drag misclick/pick; independent China/world views; redraw/editor retention; zoomed picking inverse CTM.');
})().catch(error=>{console.error(error);process.exit(1)});
