/* Offline vector cartography with screen-sized travel annotations. */
(() => {
  'use strict';
  const TYPE='atlas-edit', MAX_STOPS=200, WIDTH=1000, HEIGHT=500;
  const EXTENTS={world:[-180,-60,180,85],china:[73,15,136,55]};
  const MAP_CALIBRATION={world:{sx:1,sy:1,tx:0,ty:0},china:{sx:1,sy:1,tx:0,ty:0}};
  const MIN_ZOOM=1,MAX_ZOOM=4,ZOOM_STEP=.25;
  const viewports={china:{scale:1,x:0,y:0},world:{scale:1,x:0,y:0}};
  const model={data:null,vectorWorld:null,vectorChina:null,countries:[],countryIndex:new Map(),cities:[],cityIndex:new Map(),loading:false,error:'',promise:null,photos:[],saved:[],updatedAt:null,view:'china',lines:true,selectedId:null};
  const mapPlans=new Map(),mountedPlans=new WeakMap(),observedMaps=new WeakSet();
  let mapSequence=0,layoutScheduled=false,resizeObserver=null;
  let host=null,drag=null,suppressedClick=null;
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const icon=name=>host?.icon ? host.icon(name) : '';
  const number=value=>value===null || value===undefined || String(value).trim()==='' ? null : Number(value);
  const coordinates=(lat,lon)=>Number.isFinite(number(lat)) && Number.isFinite(number(lon)) && Math.abs(number(lat))<=90 && Math.abs(number(lon))<=180;
  const newId=()=> 'place_'+(globalThis.crypto?.randomUUID ? globalThis.crypto.randomUUID() : Date.now().toString(36)+Math.random().toString(36).slice(2));
  const modal=()=>host?.getState().modal?.type===TYPE ? host.getState().modal : null;
  const norm=value=>String(value??'').normalize('NFKC').toLocaleLowerCase().replace(/[\s·•,，。/\\|;；:：()（）\[\]{}_\-]+/g,'').replace(/市$/u,'');
  const tokens=value=>[String(value),...String(value).split(/[·•,，/\\|;；>]+/).reverse()].map(norm).filter(Boolean);
  const trimDate=value=>/^\d{4}-\d{2}-\d{2}/.test(String(value||'')) ? String(value).slice(0,10) : '';
  const copyStops=stops=>(Array.isArray(stops)?stops:[]).slice(0,MAX_STOPS).map(stop=>{
    const country=String(stop.country||countryForPlace(stop.city||stop.name)||'');
    const city=String(stop.city||(norm(stop.name)===norm(countryInfo(country)?.name)?'':stop.name)||'');
    return {id:String(stop.id||newId()),name:city||String(stop.name||countryInfo(country)?.name||''),country,city,lat:number(stop.lat),lon:number(stop.lon),date:String(stop.date||''),note:String(stop.note||''),photo_place:String(stop.photo_place||''),...(Array.isArray(stop._photo_ids)?{_photo_ids:[...stop._photo_ids]}:{})};
  });
  const short=(value,count=11)=>[...String(value)].length>count ? [...String(value)].slice(0,count-1).join('')+'…' : String(value);

  function configure(context){host=context;}
  function redraw(){host?.redraw?.();}
  function viewState(view){return viewports[view]||viewports.world;}
  function viewportBox(view){const state=viewState(view);return {x:state.x,y:state.y,width:WIDTH/state.scale,height:HEIGHT/state.scale};}
  function viewBoxValue(view){const box=viewportBox(view);return `${box.x} ${box.y} ${box.width} ${box.height}`;}
  function constrainViewport(view,scale,x,y){
    const state=viewState(view);
    state.scale=Math.max(MIN_ZOOM,Math.min(MAX_ZOOM,Number(scale)||MIN_ZOOM));
    state.x=Math.max(0,Math.min(WIDTH-WIDTH/state.scale,Number(x)||0));
    state.y=Math.max(0,Math.min(HEIGHT-HEIGHT/state.scale,Number(y)||0));
    return state;
  }
  function refreshViewport(view){
    const state=viewState(view);
    document.querySelectorAll('svg.atlas-svg').forEach(svg=>{
      if(svg.dataset.atlasView!==view)return;
      svg.setAttribute('viewBox',viewBoxValue(view));
      svg.classList.toggle('is-pannable',state.scale>MIN_ZOOM);
      layoutMap(svg);
    });
    document.querySelectorAll('[data-atlas-zoom-controls]').forEach(controls=>{
      if(controls.dataset.atlasView!==view)return;
      const level=controls.querySelector('[data-atlas-zoom-level]');
      if(level)level.textContent=`${Math.round(state.scale*100)}%`;
      const out=controls.querySelector('[data-action="atlas-zoom-out"]'),inside=controls.querySelector('[data-action="atlas-zoom-in"]'),reset=controls.querySelector('[data-action="atlas-zoom-reset"]');
      if(out)out.disabled=state.scale<=MIN_ZOOM;
      if(inside)inside.disabled=state.scale>=MAX_ZOOM;
      if(reset)reset.disabled=state.scale===MIN_ZOOM&&state.x===0&&state.y===0;
    });
  }
  function zoomView(view,scale,anchor){
    const before=viewportBox(view),next=Math.max(MIN_ZOOM,Math.min(MAX_ZOOM,scale));
    if(next===viewState(view).scale)return;
    const active=modal(),stops=active&&active.view===view?active.draft:activeStops();
    let selected=active&&active.view===view?active.selectedId:model.selectedId;
    if(view==='world'&&selected&&!String(selected).startsWith('country_'))selected='country_'+stopCountry(stops.find(stop=>stop.id===selected)||{});
    const stop=mapStops(stops,view).find(item=>item.id===selected),focus=stop?projection(view).point(stop.lon,stop.lat):null;
    const visible=focus&&focus.x>=before.x&&focus.x<=before.x+before.width&&focus.y>=before.y&&focus.y<=before.y+before.height;
    const point=anchor||(visible?focus:{x:before.x+before.width/2,y:before.y+before.height/2});
    const rx=(point.x-before.x)/before.width,ry=(point.y-before.y)/before.height;
    constrainViewport(view,next,point.x-rx*WIDTH/next,point.y-ry*HEIGHT/next);
    refreshViewport(view);
  }
  function panView(view,x,y){const state=viewState(view);constrainViewport(view,state.scale,x,y);refreshViewport(view);}
  function screenPoint(svg,clientX,clientY,inverse){
    const matrix=inverse||svg.getScreenCTM()?.inverse();if(!matrix)return null;
    const point=svg.createSVGPoint();point.x=clientX;point.y=clientY;
    return point.matrixTransform(matrix);
  }
  function zoomControls(view){
    const state=viewState(view),atMinimum=state.scale<=MIN_ZOOM,atMaximum=state.scale>=MAX_ZOOM;
    return `<div class="atlas-zoom-controls" data-atlas-zoom-controls data-atlas-view="${view}" role="group" aria-label="地图缩放" title="Ctrl + 滚轮缩放，按住地图拖动"><button class="atlas-zoom-button" type="button" data-action="atlas-zoom-out" data-atlas-view="${view}" aria-label="缩小地图" title="缩小" ${atMinimum?'disabled':''}>−</button><output class="atlas-zoom-level" data-atlas-zoom-level aria-label="当前地图倍率" aria-live="polite">${Math.round(state.scale*100)}%</output><button class="atlas-zoom-button" type="button" data-action="atlas-zoom-in" data-atlas-view="${view}" aria-label="放大地图" title="放大（Ctrl + 滚轮）" ${atMaximum?'disabled':''}>＋</button><button class="atlas-zoom-reset" type="button" data-action="atlas-zoom-reset" data-atlas-view="${view}" title="复位地图" ${atMinimum?'disabled':''}>复位</button></div>`;
  }
  function buildCityIndex(cities){
    model.cityIndex=new Map();
    model.cities=(Array.isArray(cities)?cities:[]).filter(city=>city && coordinates(city.lat,city.lon)).map(city=>({...city,lat:Number(city.lat),lon:Number(city.lon),keys:[...new Set([city.name,...(Array.isArray(city.aliases)?city.aliases:[])].map(norm).filter(Boolean))]}));
    model.cities.forEach(city=>city.keys.forEach(key=>{
      const existing=model.cityIndex.get(key)||[];
      if(!existing.some(item=>item.lat===city.lat&&item.lon===city.lon))existing.push(city);
      model.cityIndex.set(key,existing);
    }));
  }
  function countryInfo(code){return model.countryIndex.get(String(code||'').toUpperCase());}
  function countryForPlace(value,country=''){
    const direct=model.countries.find(item=>[item.code,item.iso3,item.name].some(name=>name&&norm(name)===norm(value)));
    return direct?.code||exactCity(value,country)?.country||'';
  }
  function stopCountry(stop){return stop.country||countryForPlace(stop.city||stop.name);}
  function exactCity(value,country=''){
    for(const key of tokens(value)){
      const candidates=(model.cityIndex.get(key)||[]).filter(city=>!country||city.country===country);
      if(candidates?.length===1)return candidates[0];
      if(candidates?.length>1)return null;
    }
    return null;
  }
  function citySuggestions(value,country=''){
    const keys=tokens(value);if(!keys.length)return [];
    const found=[];
    const add=city=>{if((!country||city.country===country)&&found.length<6&&!found.some(item=>item.lat===city.lat&&item.lon===city.lon))found.push(city);};
    keys.forEach(key=>(model.cityIndex.get(key)||[]).forEach(add));
    if(found.length<6){
      for(const city of model.cities){
        if(city.keys.some(alias=>keys.some(key=>alias.startsWith(key))))add(city);
        if(found.length===6)break;
      }
    }
    return found;
  }
  async function ensureLoaded(force=false){
    if(model.data && !force)return;
    if(model.promise)return model.promise;
    model.loading=true;model.error='';
    model.promise=Promise.all([host.api('/maps/atlas-data.json'),host.api('/api/atlas'),host.api('/maps/countries.json'),host.api('/maps/vector-world.json'),host.api('/maps/vector-china.json')]).then(([data,stored,countries,world,china])=>{
      if(!Array.isArray(data.features))throw new Error('离线地图资料格式不正确。');
      if(!Array.isArray(countries))throw new Error('国家目录格式不正确。');
      if(!Array.isArray(world.features)||!world.features.length||!Array.isArray(china.features)||!china.features.length)throw new Error('离线矢量地图资料不完整。');
      model.countries=countries;model.countryIndex=new Map(countries.map(country=>[country.code,country]));
      model.data=data;model.vectorWorld=world.features;model.vectorChina=china.features;buildCityIndex(data.cities);model.saved=copyStops(stored.stops);model.updatedAt=stored.updated_at??null;
    }).catch(error=>{model.error=error.message||'离线地图暂时无法打开。';throw error;}).finally(()=>{model.loading=false;model.promise=null;redraw();});
    return model.promise;
  }
  function hash(text){let value=2166136261;for(const char of String(text)){value^=char.codePointAt(0);value=Math.imul(value,16777619);}return (value>>>0).toString(36);}
  function suggestedStops(){
    const groups=new Map();
    model.photos.forEach(photo=>{
      const place=String(photo.city||photo.place||'').trim(),country=photo.country||countryForPlace(place);
      const hasGps=coordinates(photo.lat,photo.lon);
      if(!place && !hasGps && !country)return;
      const key=place ? 'place:'+country+':'+place : hasGps ? `gps:${country}:${Number(photo.lat).toFixed(4)},${Number(photo.lon).toFixed(4)}` : 'country:'+country;
      if(!groups.has(key))groups.set(key,{key,name:place||countryInfo(country)?.name||'未命名地点',place,country,photos:[]});
      groups.get(key).photos.push(photo);
    });
    const all=[...groups.values()].map(group=>{
      const ordered=[...group.photos].sort((a,b)=>(a.taken_at||'9999').localeCompare(b.taken_at||'9999'));
      const gps=ordered.find(photo=>coordinates(photo.lat,photo.lon));
      const city=gps?null:exactCity(group.place,group.country),country=group.country||city?.country||'';
      const fallback=!group.place?countryInfo(country):null;
      return {id:'suggested_'+hash(group.key),name:group.name,country,city:group.place,lat:gps?Number(gps.lat):city?.lat??fallback?.lat??null,lon:gps?Number(gps.lon):city?.lon??fallback?.lon??null,date:trimDate(ordered[0]?.taken_at),note:'',photo_place:group.place,_photo_ids:ordered.map(photo=>photo.id)};
    }).sort((a,b)=>(a.date||'9999').localeCompare(b.date||'9999')||a.name.localeCompare(b.name,'zh-CN'));
    return {all:all.slice(0,MAX_STOPS),located:all.filter(stop=>coordinates(stop.lat,stop.lon)).slice(0,MAX_STOPS),unlocated:all.filter(stop=>!coordinates(stop.lat,stop.lon))};
  }
  function coversPhotoStop(stop,existing){
    return existing.some(item=>(!stopCountry(stop)||!stopCountry(item)||stopCountry(stop)===stopCountry(item))&&(item.id===stop.id || (stop.photo_place && (norm(item.photo_place)===norm(stop.photo_place)||norm(item.name)===norm(stop.photo_place)||norm(item.name)===norm(stop.name))) || (!stop.photo_place&&coordinates(stop.lat,stop.lon)&&coordinates(item.lat,item.lon)&&Math.abs(item.lat-stop.lat)<.0001&&Math.abs(item.lon-stop.lon)<.0001)));
  }
  function activeStops(){
    const automatic=suggestedStops().all.filter(stop=>coordinates(stop.lat,stop.lon)||countryInfo(stopCountry(stop)));
    if(model.updatedAt===null)return automatic;
    return [...model.saved,...automatic.filter(stop=>!coversPhotoStop(stop,model.saved))];
  }
  function projection(view='world'){
    const extent=EXTENTS[view]||EXTENTS.world;
    const [west,south,east,north]=extent;
    const cos=Math.cos((south+north)/2*Math.PI/180);
    const scale=Math.min((WIDTH-64)/((east-west)*cos),(HEIGHT-68)/(north-south));
    const mapWidth=(east-west)*cos*scale,mapHeight=(north-south)*scale;
    const left=(WIDTH-mapWidth)/2,top=(HEIGHT-mapHeight)/2;
    const calibration=MAP_CALIBRATION[view]||MAP_CALIBRATION.world;
    const {sx,sy,tx,ty}=calibration;
    const point=(lon,lat)=>({x:(left+(Number(lon)-west)*cos*scale)*sx+tx,y:(top+(north-Number(lat))*scale)*sy+ty});
    const invert=(x,y)=>({lon:((x-tx)/sx-left)/(cos*scale)+west,lat:north-((y-ty)/sy-top)/scale});
    const contains=(lat,lon)=>Number(lon)>=west-1e-8&&Number(lon)<=east+1e-8&&Number(lat)>=south-1e-8&&Number(lat)<=north+1e-8;
    return {extent,left:left*sx+tx,top:top*sy+ty,mapWidth:mapWidth*sx,mapHeight:mapHeight*sy,calibration,point,invert,contains,
      inside:(x,y)=>{const position=invert(x,y);return x>=0&&x<=WIDTH&&y>=0&&y<=HEIGHT&&contains(position.lat,position.lon);}};
  }
  function clipRing(points,extent){
    let polygon=points;
    const [west,south,east,north]=extent;
    const boundaries=[{inside:p=>p[0]>=west,intersect:(a,b)=>[west,a[1]+(b[1]-a[1])*(west-a[0])/(b[0]-a[0])]},
      {inside:p=>p[0]<=east,intersect:(a,b)=>[east,a[1]+(b[1]-a[1])*(east-a[0])/(b[0]-a[0])]},
      {inside:p=>p[1]>=south,intersect:(a,b)=>[a[0]+(b[0]-a[0])*(south-a[1])/(b[1]-a[1]),south]},
      {inside:p=>p[1]<=north,intersect:(a,b)=>[a[0]+(b[0]-a[0])*(north-a[1])/(b[1]-a[1]),north]}];
    boundaries.forEach(boundary=>{
      const input=polygon;polygon=[];if(!input.length)return;
      let previous=input[input.length-1];
      input.forEach(current=>{
        const currentInside=boundary.inside(current),previousInside=boundary.inside(previous);
        if(currentInside){if(!previousInside)polygon.push(boundary.intersect(previous,current));polygon.push(current);}
        else if(previousInside)polygon.push(boundary.intersect(previous,current));
        previous=current;
      });
    });
    return polygon.filter(point=>point.every(Number.isFinite));
  }
  function ringPaths(ring,proj){
    const clean=(Array.isArray(ring)?ring:[]).filter(point=>Array.isArray(point)&&Number.isFinite(Number(point[0]))&&Number.isFinite(Number(point[1]))).map(point=>[Number(point[0]),Number(point[1])]);
    if(clean.length<3)return '';
    const unwrapped=[];
    clean.forEach(([lon,lat])=>{
      if(unwrapped.length){const previous=unwrapped[unwrapped.length-1][0];while(lon-previous>180)lon-=360;while(lon-previous< -180)lon+=360;}
      unwrapped.push([lon,lat]);
    });
    return [-360,0,360].map(shift=>clipRing(unwrapped.map(([lon,lat])=>[lon+shift,lat]),proj.extent)).filter(points=>points.length>=3).map(points=>points.map(([lon,lat],index)=>{const point=proj.point(lon,lat);return `${index?'L':'M'}${point.x.toFixed(2)},${point.y.toFixed(2)}`;}).join('')+'Z').join('');
  }
  function featurePath(geometry,proj){
    if(!geometry)return '';
    const polygons=geometry.type==='Polygon'?[geometry.coordinates]:geometry.type==='MultiPolygon'?geometry.coordinates:[];
    return (polygons||[]).map(polygon=>(polygon||[]).map(ring=>ringPaths(ring,proj)).join('')).join('');
  }
  function photoCount(stop){
    if(stop._countryStops)return new Set(stop._countryStops.flatMap(item=>photoIds(item))).size;
    return photoIds(stop).length;
  }
  function photoIds(stop){
    if(stop.photo_place)return model.photos.filter(photo=>String(photo.city||photo.place||'')===stop.photo_place&&(!stopCountry(stop)||(photo.country||countryForPlace(photo.city||photo.place))===stopCountry(stop))).map(photo=>photo.id);
    return Array.isArray(stop._photo_ids)?stop._photo_ids.filter(id=>model.photos.some(photo=>photo.id===id)):[];
  }
  function countryGroups(stops){
    const groups=new Map();
    for(const stop of stops){
      const country=countryInfo(stopCountry(stop));if(!country)continue;
      if(!groups.has(country.code))groups.set(country.code,{id:'country_'+country.code,name:country.name,country:country.code,city:'',lat:country.lat,lon:country.lon,date:stop.date||'',note:'',photo_place:'',_countryStops:[]});
      const group=groups.get(country.code);group._countryStops.push(stop);
      if(stop.date&&(!group.date||stop.date<group.date))group.date=stop.date;
    }
    return [...groups.values()];
  }
  function mapStops(stops,view){
    return view==='world'?countryGroups(stops):stops.filter(stop=>(!stopCountry(stop)||['CN','TW','HK','MO'].includes(stopCountry(stop)))&&coordinates(stop.lat,stop.lon));
  }
  function mapRoute(stops,view){
    if(view!=='world')return mapStops(stops,view);
    const groups=new Map(countryGroups(stops).map(stop=>[stop.country,stop])),route=[];
    for(const stop of stops){const country=stopCountry(stop);if(groups.has(country)&&route[route.length-1]?.country!==country)route.push(groups.get(country));}
    return route;
  }
  function visiblePoint(proj,lon,lat){
    const point=proj.point(lon,lat);
    return {x:Math.max(14,Math.min(WIDTH-14,point.x)),y:Math.max(14,Math.min(HEIGHT-14,point.y))};
  }
  function screenMetrics(view,svg=null){
    const box=viewportBox(view),matrix=svg?.getScreenCTM?.();
    const measured=matrix?Math.hypot(matrix.a,matrix.b||0):0;
    const scale=Number.isFinite(measured)&&measured>0?measured:viewState(view).scale;
    return {box,scale,units:1/scale,width:box.width*scale,height:box.height*scale};
  }
  const mapPixel=(point,metrics)=>({x:(point.x-metrics.box.x)*metrics.scale,y:(point.y-metrics.box.y)*metrics.scale});
  const visiblePin=(point,metrics)=>point.x>=-12&&point.x<=metrics.width+12&&point.y>=-12&&point.y<=metrics.height+12;
  function labelPositions(pins,selected,metrics,curves=[]){
    const boxes=[],labels=new Map(),fontSize=14;
    const routePoints=curves.flatMap(segment=>[.2,.35,.5,.65,.8].map(t=>segment.point(t))).filter(point=>point.x>=0&&point.y>=0&&point.x<=metrics.width&&point.y<=metrics.height);
    const visible=pins.filter(pin=>visiblePin(pin.point,metrics));
    const ordered=[...visible].sort((a,b)=>Number(b.stop.id===selected)-Number(a.stop.id===selected));
    ordered.forEach(pin=>{
      const text=short(pin.stop.name),width=Math.max(20,[...text].reduce((sum,char)=>sum+(/[\u0000-\u007f]/.test(char)?fontSize*.57:fontSize),0));
      const choices=[{dx:14,dy:-12,anchor:'start'},{dx:14,dy:25,anchor:'start'},{dx:-14,dy:-12,anchor:'end'},{dx:-14,dy:25,anchor:'end'},{dx:0,dy:-28,anchor:'middle'},{dx:0,dy:41,anchor:'middle'}];
      let best=null,bestScore=Infinity;
      for(const choice of choices){
        const x=pin.point.x+choice.dx,y=pin.point.y+choice.dy;
        const left=choice.anchor==='end'?x-width:choice.anchor==='middle'?x-width/2:x;
        const box={left:left-5,right:left+width+5,top:y-fontSize-3,bottom:y+5};
        if(box.left<4||box.right>metrics.width-4||box.top<4||box.bottom>metrics.height-4)continue;
        if(boxes.some(other=>box.left<other.right+5&&box.right>other.left-5&&box.top<other.bottom+3&&box.bottom>other.top-3))continue;
        if(visible.some(other=>other!==pin&&other.point.x>box.left-8&&other.point.x<box.right+8&&other.point.y>box.top-8&&other.point.y<box.bottom+8))continue;
        const score=routePoints.filter(point=>point.x>box.left-10&&point.x<box.right+10&&point.y>box.top-10&&point.y<box.bottom+10).length;
        if(score<bestScore){best={x,y,anchor:choice.anchor,text,box};bestScore=score;}
        if(score===0)break;
      }
      if(best){boxes.push(best.box);labels.set(pin.stop.id,best);}
      if(!labels.has(pin.stop.id)&&pin.stop.id===selected){
        const left=Math.max(4,Math.min(metrics.width-width-14,pin.point.x+14));
        const top=Math.max(4,Math.min(metrics.height-30,pin.point.y-32));
        const box={left,right:left+width+10,top,bottom:top+22};
        boxes.push(box);labels.set(pin.stop.id,{x:left+5,y:top+17,anchor:'start',text,box});
      }
    });
    return labels;
  }
  function curve(from,to){
    const dx=to.x-from.x,dy=to.y-from.y,length=Math.hypot(dx,dy);
    if(length<.5)return null;
    const bend=2*Math.max(22,Math.min(38,length*.12));
    const control={x:(from.x+to.x)/2-dy/length*bend,y:(from.y+to.y)/2+dx/length*bend};
    const point=t=>({x:(1-t)*(1-t)*from.x+2*(1-t)*t*control.x+t*t*to.x,y:(1-t)*(1-t)*from.y+2*(1-t)*t*control.y+t*t*to.y});
    const tangent=t=>({x:2*((1-t)*(control.x-from.x)+t*(to.x-control.x)),y:2*((1-t)*(control.y-from.y)+t*(to.y-control.y))});
    const samples=[{t:0,distance:0,point:from}];
    for(let i=1;i<=32;i++){const p=point(i/32),previous=samples[i-1];samples.push({t:i/32,point:p,distance:previous.distance+Math.hypot(p.x-previous.point.x,p.y-previous.point.y)});}
    const atDistance=distance=>{const next=samples.find(sample=>sample.distance>=distance)||samples[samples.length-1],index=samples.indexOf(next),previous=samples[Math.max(0,index-1)];return previous.t+(next.t-previous.t)*(distance-previous.distance)/(next.distance-previous.distance||1);};
    return {d:`M${from.x.toFixed(2)},${from.y.toFixed(2)}Q${control.x.toFixed(2)},${control.y.toFixed(2)} ${to.x.toFixed(2)},${to.y.toFixed(2)}`,point,tangent,length:samples[samples.length-1].distance,atDistance};
  }
  function routeCurves(from,to,proj,view,metrics){
    const p1=mapPixel(visiblePoint(proj,from.lon,from.lat),metrics),p2=mapPixel(visiblePoint(proj,to.lon,to.lat),metrics);
    if(view==='world'&&Math.abs(to.lon-from.lon)>180){
      const lon2=to.lon+(to.lon>from.lon?-360:360),edge=lon2>180?180:-180;
      const lat=from.lat+(to.lat-from.lat)*(edge-from.lon)/(lon2-from.lon);
      return [curve(p1,mapPixel(visiblePoint(proj,edge,lat),metrics)),curve(mapPixel(visiblePoint(proj,-edge,lat),metrics),p2)].filter(Boolean);
    }
    return [curve(p1,p2)].filter(Boolean);
  }
  function arrowsMarkup(curves,metrics,pins,labels){
    const placed=[],boxes=[...labels.values()].map(label=>label.box);
    return curves.flatMap(segment=>{
      const count=Math.max(1,Math.floor(segment.length/150));
      return Array.from({length:count},(_,i)=>{
        const middle=(i+.5)*segment.length/count;
        const candidates=[0,-18,18,-30,30].map(offset=>Math.max(segment.length*.15,Math.min(segment.length*.85,middle+offset)));
        for(const distance of candidates){
          const t=segment.atDistance(distance),point=segment.point(t),direction=segment.tangent(t);
          if(point.x<13||point.y<13||point.x>metrics.width-13||point.y>metrics.height-13)continue;
          if(pins.some(pin=>Math.hypot(pin.point.x-point.x,pin.point.y-point.y)<20))continue;
          if(boxes.some(box=>point.x>box.left-10&&point.x<box.right+10&&point.y>box.top-10&&point.y<box.bottom+10))continue;
          if(placed.some(other=>Math.hypot(other.x-point.x,other.y-point.y)<44))continue;
          placed.push(point);
          const angle=Math.atan2(direction.y,direction.x)*180/Math.PI;
          const shape='M-7 0H7M1-5.5 7 0 1 5.5';
          return `<g class="atlas-route-arrow" transform="translate(${point.x.toFixed(2)} ${point.y.toFixed(2)}) rotate(${angle.toFixed(2)})" aria-hidden="true"><path class="atlas-route-arrow-underlay" d="${shape}" fill="none" stroke="#fffaf0" stroke-width="6" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/><path class="atlas-route-arrow-mark" d="${shape}" fill="none" stroke="#a44935" stroke-width="2.6" stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/></g>`;
        }
        return '';
      });
    }).join('');
  }
  function planLayout(plan,metrics){
    const pins=plan.pins.map(pin=>({...pin,point:mapPixel(pin.point,metrics)}));
    const routes=plan.routes.map(route=>routeCurves(route.from,route.to,plan.proj,plan.view,metrics));
    const labels=labelPositions(pins,plan.selectedId,metrics,routes.flat());
    return {pins,labels,routes,arrows:arrowsMarkup(routes.flat(),metrics,pins,labels)};
  }
  function pinMarkup(pin,label,plan,metrics){
    const {stop,point}=pin,selected=stop.id===plan.selectedId,visible=visiblePin(point,metrics);
    const box=label?.box;
    return `<g class="atlas-pin ${selected?'is-selected':''}" data-atlas-pin="${esc(stop.id)}" data-action="atlas-select" data-atlas-id="${esc(stop.id)}" role="button" tabindex="${visible?0:-1}" aria-hidden="${!visible}" ${visible?'':'display="none"'} transform="translate(${point.x.toFixed(2)} ${point.y.toFixed(2)})" aria-label="${esc(stop.name)}${stop.date?`，${esc(stop.date)}`:''}，${photoCount(stop)} 张照片"><title>${esc(stop.name)}${stop.date?` · ${esc(stop.date)}`:''}</title><circle class="atlas-pin-hit" r="12" fill="transparent" stroke="none" pointer-events="all"/><circle class="atlas-pin-halo" r="${selected?13:10}" fill="#d86d59" opacity="${selected?.22:.13}"/><circle class="atlas-pin-focus" r="10" fill="none" stroke="#b65643" stroke-width="1.5" opacity="0" vector-effect="non-scaling-stroke"/><circle class="atlas-pin-dot" r="${selected?6:5}" fill="#c76851" stroke="#fffaf0" stroke-width="2" vector-effect="non-scaling-stroke"/><rect class="atlas-pin-label-background" x="${box?(box.left-point.x).toFixed(2):0}" y="${box?(box.top-point.y).toFixed(2):0}" width="${box?(box.right-box.left).toFixed(2):0}" height="${box?(box.bottom-box.top).toFixed(2):0}" rx="4" fill="#fffaf0" stroke="#d4cdbd" stroke-width=".7" vector-effect="non-scaling-stroke" opacity=".96" ${label?'':'visibility="hidden"'}/><text class="atlas-pin-label" x="${label?(label.x-point.x).toFixed(2):0}" y="${label?(label.y-point.y).toFixed(2):0}" text-anchor="${label?.anchor||'start'}" font-size="14" fill="#435c5d" ${label?'':'visibility="hidden"'}>${esc(label?.text||short(stop.name))}</text></g>`;
  }
  function screenLayerMarkup(plan,metrics){
    const layout=planLayout(plan,metrics);
    const routes=layout.routes.map((segments,index)=>{const d=segments.map(segment=>segment.d).join('');return `<g class="atlas-route" data-route-index="${index}" pointer-events="none"><path class="atlas-route-line-underlay" d="${d}" fill="none" stroke="#fffaf0" stroke-width="5" stroke-linecap="round" opacity=".95" vector-effect="non-scaling-stroke"/><path class="atlas-route-line" d="${d}" fill="none" stroke="#c76851" stroke-width="2.2" stroke-dasharray="5 5" stroke-linecap="round" vector-effect="non-scaling-stroke"/></g>`;}).join('');
    return `<g class="atlas-screen-layer" transform="translate(${metrics.box.x} ${metrics.box.y}) scale(${metrics.units})">${routes}<g class="atlas-route-arrows" pointer-events="none">${layout.arrows}</g>${layout.pins.map(pin=>pinMarkup(pin,layout.labels.get(pin.stop.id),plan,metrics)).join('')}</g>`;
  }
  function layoutMap(svg){
    const plan=mountedPlans.get(svg)||mapPlans.get(svg.dataset.atlasPlan);
    if(!plan||!svg.querySelector)return;
    mountedPlans.set(svg,plan);
    const metrics=screenMetrics(plan.view,svg),layout=planLayout(plan,metrics),layer=svg.querySelector('.atlas-screen-layer');
    if(!layer)return;
    layer.setAttribute('transform',`translate(${metrics.box.x} ${metrics.box.y}) scale(${metrics.units})`);
    svg.querySelectorAll('[data-route-index]').forEach(node=>{
      const d=(layout.routes[Number(node.dataset.routeIndex)]||[]).map(segment=>segment.d).join('');
      node.querySelectorAll('path').forEach(path=>path.setAttribute('d',d));
    });
    const arrows=svg.querySelector('.atlas-route-arrows');if(arrows)arrows.innerHTML=layout.arrows;
    const pinIndex=new Map(layout.pins.map(pin=>[pin.stop.id,pin]));
    svg.querySelectorAll('[data-atlas-pin]').forEach(node=>{
      const pin=pinIndex.get(node.dataset.atlasPin);if(!pin)return;
      const point=pin.point,visible=visiblePin(point,metrics),label=layout.labels.get(pin.stop.id),rect=node.querySelector('.atlas-pin-label-background'),text=node.querySelector('.atlas-pin-label');
      node.setAttribute('transform',`translate(${point.x.toFixed(2)} ${point.y.toFixed(2)})`);
      node.setAttribute('tabindex',visible?'0':'-1');node.setAttribute('aria-hidden',String(!visible));
      if(visible)node.removeAttribute('display');else node.setAttribute('display','none');
      for(const element of [rect,text])if(element){if(label)element.removeAttribute('visibility');else element.setAttribute('visibility','hidden');}
      if(label){
        if(rect)for(const [key,value] of Object.entries({x:label.box.left-point.x,y:label.box.top-point.y,width:label.box.right-label.box.left,height:label.box.bottom-label.box.top}))rect.setAttribute(key,value.toFixed(2));
        if(text){text.setAttribute('x',(label.x-point.x).toFixed(2));text.setAttribute('y',(label.y-point.y).toFixed(2));text.setAttribute('text-anchor',label.anchor);}
      }
    });
    if(resizeObserver&&!observedMaps.has(svg)){observedMaps.add(svg);resizeObserver.observe(svg);}
  }
  function refreshMapLayouts(){document.querySelectorAll('svg.atlas-svg').forEach(layoutMap);}
  function scheduleMapLayout(){
    if(layoutScheduled)return;layoutScheduled=true;
    (window.requestAnimationFrame||((callback)=>setTimeout(callback,0)))(()=>{layoutScheduled=false;refreshMapLayouts();});
  }
  function renderMap(stops,view,selectedId,editing=false){
    const proj=projection(view),source=Array.isArray(stops)?stops:[],all=mapStops(source,view);
    if(editing&&view==='world'&&!String(selectedId||'').startsWith('country_'))selectedId='country_'+stopCountry(source.find(stop=>stop.id===selectedId)||{});
    const pins=all.filter(stop=>proj.contains(stop.lat,stop.lon)).map(stop=>({stop,point:visiblePoint(proj,stop.lon,stop.lat)}));
    const itinerary=mapRoute(editing?source:(model.updatedAt!==null?model.saved:suggestedStops().all),view).filter(stop=>coordinates(stop.lat,stop.lon));
    const routes=model.lines?itinerary.slice(1).flatMap((stop,index)=>{const from=itinerary[index];return proj.contains(from.lat,from.lon)&&proj.contains(stop.lat,stop.lon)?[{from,to:stop}]:[];}):[];
    const plan={pins,routes,proj,view,selectedId},id='atlas_'+(++mapSequence);
    mapPlans.set(id,plan);if(mapPlans.size>16)mapPlans.delete(mapPlans.keys().next().value);
    const art=window.ShiguangAtlasArt.render(view,model.vectorWorld||[],model.vectorChina||[],proj,featurePath,id);
    scheduleMapLayout();
    return `<svg class="atlas-svg ${viewState(view).scale>MIN_ZOOM?'is-pannable':''}" viewBox="${viewBoxValue(view)}" role="group" aria-label="${view==='china'?'中国':'世界'}足迹地图${editing?'，可选择地点位置':''}" data-atlas-view="${view}" data-atlas-plan="${id}" xmlns="http://www.w3.org/2000/svg"><title>${view==='china'?'中国':'世界'}足迹路线</title>${art}${screenLayerMarkup(plan,screenMetrics(view))}</svg>`;
  }

  function controls(view,editing=false){return `<div class="atlas-controls"><div class="atlas-tabs" role="group" aria-label="地图范围"><button class="atlas-tab ${view==='china'?'active':''}" data-action="atlas-view" data-scope="china" aria-pressed="${view==='china'}" type="button">中国</button><button class="atlas-tab ${view==='world'?'active':''}" data-action="atlas-view" data-scope="world" aria-pressed="${view==='world'}" type="button">世界</button></div>${zoomControls(view)}<label class="atlas-lines-control"><input type="checkbox" data-atlas-lines ${model.lines?'checked':''}>连接足迹</label>${!editing?`<button class="button secondary atlas-edit-button" data-action="atlas-edit" ${model.loading?'disabled':''}>${icon('edit')} 编辑足迹</button>`:''}</div>`;}
  function renderPinDetail(stop){
    if(!stop)return '';
    const count=photoCount(stop);
    const cityNames=stop._countryStops?[...new Set(stop._countryStops.map(item=>item.city||item.name).filter(name=>name&&name!==stop.name))]:[];
    return `<div class="atlas-pin-detail"><div class="atlas-pin-copy"><h4>${esc(stop.name)}</h4><p>${[stop.date?esc(stop.date):'',cityNames.length?`${cityNames.length} 座城市`:'',count?`${count} 张照片`:''].filter(Boolean).join(' · ')}</p>${cityNames.length?`<p>${esc(cityNames.join(' · '))}</p>`:''}${stop.note?`<p>${esc(stop.note)}</p>`:''}</div>${count&&(stop._countryStops||stop.photo_place)?`<button class="button secondary small" data-action="${stop._countryStops?'atlas-open-country':'atlas-open-place'}" data-country="${esc(stop.country||'')}" data-place="${esc(stop.photo_place)}">查看照片 ${icon('right')}</button>`:''}</div>`;
  }
  function render(photos){
    model.photos=Array.isArray(photos)?photos:[];
    if(!model.data&&!model.loading&&!model.error)ensureLoaded().catch(()=>{});
    const stops=activeStops(),suggestions=suggestedStops();
    const unlocated=model.updatedAt!==null?suggestions.unlocated.filter(stop=>!coversPhotoStop(stop,model.saved)):suggestions.unlocated;
    const selected=mapStops(stops,model.view).find(stop=>stop.id===model.selectedId);
    const content=model.loading?'<div class="loading-state"><span class="spinner"></span></div>':model.error?`<div class="atlas-error"><p>${esc(model.error)}</p><button class="button secondary" data-action="atlas-retry">重新加载</button></div>`:renderMap(stops,model.view,model.selectedId);
    return `<section class="atlas-panel" aria-label="拍摄地点足迹地图"><div class="atlas-toolbar"><h3>足迹路线</h3>${controls(model.view)}</div><div class="atlas-map-frame">${content}</div>${renderPinDetail(selected)}${unlocated.length&&!model.loading&&!model.error?`<div class="atlas-unlocated"><span>${unlocated.length} 个拍摄地点待定位</span><button class="inline-link" data-action="atlas-edit-unlocated">编辑位置</button></div>`:''}<div class="atlas-map-caption"><span>${model.view==='china'?'中国 · 城市足迹':'世界 · 国家足迹'}</span><small><a href="https://www.naturalearthdata.com/" target="_blank" rel="noopener noreferrer">Natural Earth</a> · <a href="https://www.geonames.org/" target="_blank" rel="noopener noreferrer">GeoNames</a> <a href="https://creativecommons.org/licenses/by/4.0/" target="_blank" rel="noopener noreferrer">CC BY 4.0</a> · 矢量手绘</small></div></section>`;
  }
  function refreshMapPreview(){
    const active=modal();if(!active)return;
    const target=document.getElementById('atlas-editor-map');
    if(target){target.innerHTML=renderMap(active.draft,active.view,active.selectedId,true);target.classList.toggle('is-picking',Boolean(active.picking));}
  }
  function suggestionMarkup(stop){
    return citySuggestions(stop.city||'',stop.country).map(city=>{const label=exactCity(stop.city,stop.country)===city?stop.city:city.aliases?.filter(alias=>/^[\u3400-\u9fff]{2,8}$/u.test(alias)).sort((a,b)=>a.length-b.length)[0]||city.name;return `<button class="atlas-city-suggestion" type="button" data-action="atlas-use-city" data-atlas-id="${esc(stop.id)}" data-country="${esc(city.country)}" data-city="${esc(label)}" data-lat="${city.lat}" data-lon="${city.lon}">${esc(label)} <small>${esc(countryInfo(city.country)?.name||city.country)}</small></button>`;}).join('');
  }
  function renderEditor(active){
    const disabled=active.busy?'disabled':'',places=[...new Set([...model.photos.map(photo=>String(photo.city||photo.place||'')),...active.draft.map(stop=>stop.photo_place)].filter(Boolean))].sort((a,b)=>a.localeCompare(b,'zh-CN'));
    return `<form class="atlas-editor" id="atlas-editor-form"><div class="atlas-editor-heading"><h3>编辑足迹</h3><span>${active.draft.length} / ${MAX_STOPS}</span></div><div class="atlas-stop-list">${active.draft.map((stop,index)=>`<section class="atlas-stop-row ${active.selectedId===stop.id?'is-selected':''}" data-atlas-stop="${esc(stop.id)}"><span class="atlas-stop-number">${String(index+1).padStart(2,'0')}</span><div class="atlas-stop-fields"><div class="field"><label for="atlas-country-${esc(stop.id)}">国家</label><select id="atlas-country-${esc(stop.id)}" data-atlas-field="country" data-atlas-id="${esc(stop.id)}" required ${disabled}><option value="">选择国家</option>${model.countries.map(country=>`<option value="${esc(country.code)}" ${stop.country===country.code?'selected':''}>${esc(country.name)}</option>`).join('')}</select></div><div class="field"><label for="atlas-city-${esc(stop.id)}">城市 <span class="muted">选填</span></label><input id="atlas-city-${esc(stop.id)}" data-atlas-field="city" data-atlas-id="${esc(stop.id)}" maxlength="100" value="${esc(stop.city||'')}" placeholder="城市或景点" ${disabled} autocomplete="off"></div><div class="atlas-city-suggestions" id="atlas-suggestions-${esc(stop.id)}">${active.selectedId===stop.id?suggestionMarkup(stop):''}</div><div class="field"><label for="atlas-date-${esc(stop.id)}">到访日期</label><input id="atlas-date-${esc(stop.id)}" data-atlas-field="date" data-atlas-id="${esc(stop.id)}" type="date" value="${esc(stop.date)}" ${disabled}></div><div class="field atlas-photo-place-field"><label for="atlas-place-${esc(stop.id)}">关联拍摄地点</label><select id="atlas-place-${esc(stop.id)}" data-atlas-field="photo_place" data-atlas-id="${esc(stop.id)}" ${disabled}><option value="">不关联照片</option>${places.map(place=>`<option value="${esc(place)}" ${stop.photo_place===place?'selected':''}>${esc(place)}</option>`).join('')}</select></div><div class="field atlas-note-field"><label for="atlas-note-${esc(stop.id)}">备注 <span class="muted">选填</span></label><textarea id="atlas-note-${esc(stop.id)}" data-atlas-field="note" data-atlas-id="${esc(stop.id)}" rows="2" maxlength="500" ${disabled}>${esc(stop.note)}</textarea></div><details class="atlas-coordinate-fields"><summary>精确位置</summary><div class="field"><label for="atlas-lat-${esc(stop.id)}">纬度</label><input id="atlas-lat-${esc(stop.id)}" data-atlas-field="lat" data-atlas-id="${esc(stop.id)}" type="number" min="-90" max="90" step="any" value="${stop.lat??''}" ${disabled}></div><div class="field"><label for="atlas-lon-${esc(stop.id)}">经度</label><input id="atlas-lon-${esc(stop.id)}" data-atlas-field="lon" data-atlas-id="${esc(stop.id)}" type="number" min="-180" max="180" step="any" value="${stop.lon??''}" ${disabled}></div></details><button class="button secondary small atlas-pick-button" type="button" data-action="atlas-pick" data-atlas-id="${esc(stop.id)}" ${disabled}>${icon('pin')} ${coordinates(stop.lat,stop.lon)?'地图重新定位':'地图定位'}</button></div><div class="atlas-stop-actions"><button class="button ghost small" type="button" data-action="atlas-up" data-atlas-index="${index}" title="上移" aria-label="上移第 ${index+1} 个地点" ${index===0||active.busy?'disabled':''}>↑</button><button class="button ghost small" type="button" data-action="atlas-down" data-atlas-index="${index}" title="下移" aria-label="下移第 ${index+1} 个地点" ${index===active.draft.length-1||active.busy?'disabled':''}>↓</button><button class="button ghost small" type="button" data-action="atlas-remove" data-atlas-index="${index}" title="删除" aria-label="删除第 ${index+1} 个地点" ${disabled}>×</button></div></section>`).join('')}</div><div class="atlas-editor-additions"><button class="button secondary" type="button" data-action="atlas-add-photos" ${disabled}>${icon('photo')} 加入照片地点</button><button class="button secondary atlas-add-stop" type="button" data-action="atlas-add" ${active.draft.length>=MAX_STOPS||active.busy?'disabled':''}>${icon('plus')} 添加地点</button></div></form>`;
  }
  function renderModal(active){
    const selected=active.draft.find(stop=>stop.id===active.selectedId);
    const body=`<div class="atlas-editor-toolbar">${controls(active.view,true)}</div><div class="atlas-editor-map ${active.picking?'is-picking':''}" id="atlas-editor-map">${renderMap(active.draft,active.view,active.selectedId,true)}</div><div class="atlas-pick-status">${active.picking?`在地图上选择「${esc(selected?.name||'新地点')}」的位置`:'足迹路线示意'}</div>${renderEditor(active)}`;
    return host.modalFrame('编辑足迹','',body,`<button class="button ghost" data-action="close-modal" ${active.busy?'disabled':''}>取消</button><button class="button primary" data-action="atlas-save" ${active.busy?'disabled':''}>${icon('check')} ${active.busy?'正在保存':'保存足迹'}</button>`,'atlas-edit-modal');
  }
  function focusStop(id,field='city'){
    setTimeout(()=>{const input=document.getElementById(`atlas-${field}-${id}`);const details=input?.closest('details');if(details)details.open=true;input?.focus();input?.scrollIntoView({block:'nearest',behavior:'smooth'});},0);
  }
  function captureDraft(){
    const active=modal();if(!active)return;
    document.querySelectorAll('#atlas-editor-form [data-atlas-field]').forEach(field=>{
      const stop=active.draft.find(item=>item.id===field.dataset.atlasId);if(!stop)return;
      stop[field.dataset.atlasField]=['lat','lon'].includes(field.dataset.atlasField)?number(field.value):field.value;
    });
    active.draft.forEach(stop=>{stop.name=stop.city?.trim()||countryInfo(stop.country)?.name||stop.name;});
  }
  async function openEditor(includeUnlocated=false){
    await ensureLoaded();
    const draft=copyStops(model.updatedAt!==null?model.saved:suggestedStops().all);
    if(includeUnlocated){
      const pending=suggestedStops().unlocated.filter(stop=>!coversPhotoStop(stop,draft));
      if(draft.length+pending.length>MAX_STOPS)throw new Error(`最多保存 ${MAX_STOPS} 个地点，请先减少已有地点。`);
      draft.push(...copyStops(pending));
    }
    host.showModal(TYPE,{draft,view:model.view,selectedId:draft[0]?.id||null,picking:false});
  }
  async function save(){
    const active=modal();if(!active||active.busy)return;
    captureDraft();
    const invalidName=active.draft.find(stop=>!stop.name.trim());
    if(invalidName){focusStop(invalidName.id,'country');throw new Error('请填写地点名称。');}
    const invalidCountry=active.draft.find(stop=>!countryInfo(stop.country));
    if(invalidCountry){focusStop(invalidCountry.id,'country');throw new Error('请选择足迹所在的国家。');}
    const invalidPosition=active.draft.find(stop=>!coordinates(stop.lat,stop.lon));
    if(invalidPosition){active.selectedId=invalidPosition.id;active.picking=true;host.renderModal();focusStop(invalidPosition.id,'lat');throw new Error(`请为「${invalidPosition.name}」选择地图位置，或使用城市建议。`);}
    const form=document.getElementById('atlas-editor-form');if(form&&!form.reportValidity())return;
    const stops=active.draft.map(({id,name,country,city,lat,lon,date,note,photo_place})=>({id,name:name.trim(),country,city:city.trim(),lat:Number(lat),lon:Number(lon),date,note:note.trim(),photo_place}));
    active.busy=true;host.renderModal();
    try{
      const result=await host.api('/api/atlas',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({stops})});
      model.saved=copyStops(result.stops);model.updatedAt=result.updated_at??new Date().toISOString();model.selectedId=null;
      active.busy=false;host.closeModal(true);redraw();host.toast('足迹已保存。');
    }finally{active.busy=false;if(modal()===active)host.renderModal();}
  }
  function setCoordinates(stop,lat,lon){
    stop.lat=Number(Number(lat).toFixed(5));stop.lon=Number(Number(lon).toFixed(5));
    const latField=document.getElementById(`atlas-lat-${stop.id}`),lonField=document.getElementById(`atlas-lon-${stop.id}`);
    if(latField)latField.value=String(stop.lat);if(lonField)lonField.value=String(stop.lon);
  }
  async function handleAction(action,element){
    if(!action.startsWith('atlas-'))return false;
    if(!host)throw new Error('足迹地图暂时无法使用，请重新打开相册。');
    const active=modal();if(active?.busy)return true;
    if(['atlas-zoom-in','atlas-zoom-out','atlas-zoom-reset'].includes(action)){
      const requested=element.dataset.atlasView||active?.view||model.view,view=requested==='china'?'china':'world';
      if(action==='atlas-zoom-reset'){constrainViewport(view,MIN_ZOOM,0,0);refreshViewport(view);}
      else zoomView(view,Math.round((viewState(view).scale+(action==='atlas-zoom-in'?ZOOM_STEP:-ZOOM_STEP))*100)/100);
      return true;
    }
    if(action==='atlas-retry'){await ensureLoaded(true);return true;}
    if(action==='atlas-edit'||action==='atlas-edit-unlocated'){await openEditor(action==='atlas-edit-unlocated');return true;}
    if(action==='atlas-view'){
      const view=element.dataset.scope==='world'?'world':'china';model.view=view;
      if(active){captureDraft();active.view=view;host.renderModal();}else redraw();
      setTimeout(()=>document.querySelector(`${active?'.atlas-edit-modal':'.atlas-panel'} [data-scope="${view}"]`)?.focus(),0);return true;
    }
    if(action==='atlas-select'){
      let id=element.dataset.atlasId;
      if(active&&id.startsWith('country_'))id=active.draft.find(stop=>stopCountry(stop)===id.slice(8))?.id||id;
      if(active){captureDraft();active.selectedId=id;active.picking=false;host.renderModal();focusStop(id);}else{
        model.selectedId=id;redraw();
        setTimeout(()=>[...document.querySelectorAll('[data-atlas-pin]')].find(pin=>pin.dataset.atlasPin===id)?.focus(),0);
      }
      return true;
    }
    if(action==='atlas-open-place'){host.openPlace(element.dataset.place,element.dataset.country);return true;}
    if(action==='atlas-open-country'){host.openCountry?.(element.dataset.country);return true;}
    if(!active)return true;
    if(action==='atlas-save'){await save();return true;}
    captureDraft();
    if(action==='atlas-add-photos'){
      const additions=suggestedStops().all.filter(stop=>coordinates(stop.lat,stop.lon)||countryInfo(stopCountry(stop))).filter(stop=>!coversPhotoStop(stop,active.draft));
      if(active.draft.length+additions.length>MAX_STOPS)throw new Error(`最多保存 ${MAX_STOPS} 个地点，请先减少已有地点。`);
      active.draft.push(...copyStops(additions));host.renderModal();
      if(additions.length)focusStop(additions[0].id);else host.toast('照片中的可定位地点已经在路线中。');
      return true;
    }
    if(action==='atlas-add'){
      if(active.draft.length>=MAX_STOPS)throw new Error(`最多保存 ${MAX_STOPS} 个地点。`);
      const cn=countryInfo('CN');const stop={id:newId(),name:cn?.name||'',country:'CN',city:'',lat:cn?.lat??null,lon:cn?.lon??null,date:'',note:'',photo_place:''};active.draft.push(stop);active.selectedId=stop.id;active.picking=false;host.renderModal();focusStop(stop.id);return true;
    }
    if(action==='atlas-pick'){
      active.selectedId=element.dataset.atlasId;active.picking=true;host.renderModal();document.getElementById('atlas-editor-map')?.scrollIntoView({block:'nearest',behavior:'smooth'});return true;
    }
    if(action==='atlas-use-city'){
      const stop=active.draft.find(item=>item.id===element.dataset.atlasId);
      if(stop&&coordinates(element.dataset.lat,element.dataset.lon)){stop.country=element.dataset.country||stop.country;stop.city=element.dataset.city||stop.city;stop.name=stop.city||countryInfo(stop.country)?.name||stop.name;setCoordinates(stop,element.dataset.lat,element.dataset.lon);active.selectedId=stop.id;active.picking=false;host.renderModal();focusStop(stop.id);}return true;
    }
    const index=Number(element.dataset.atlasIndex);if(!Number.isInteger(index)||index<0||index>=active.draft.length)return true;
    if(action==='atlas-remove'){active.draft.splice(index,1);if(!active.draft.some(stop=>stop.id===active.selectedId)){active.selectedId=active.draft[Math.min(index,active.draft.length-1)]?.id||null;active.picking=false;}host.renderModal();return true;}
    const next=action==='atlas-up'?index-1:action==='atlas-down'?index+1:index;
    if(next>=0&&next<active.draft.length&&next!==index){[active.draft[index],active.draft[next]]=[active.draft[next],active.draft[index]];host.renderModal();focusStop(active.draft[next].id);}
    return true;
  }
  function inputChanged(event){
    const field=event.target,active=modal();
    if(field.dataset?.atlasLines!==undefined){model.lines=field.checked;if(active)refreshMapPreview();else redraw();return;}
    if(!active||active.busy||!field.dataset?.atlasField)return;
    const stop=active.draft.find(item=>item.id===field.dataset.atlasId);if(!stop)return;
    stop[field.dataset.atlasField]=['lat','lon'].includes(field.dataset.atlasField)?number(field.value):field.value;active.selectedId=stop.id;
    if(['city','country'].includes(field.dataset.atlasField)){
      const city=exactCity(stop.city,stop.country),country=countryInfo(stop.country);
      if(field.dataset.atlasField==='country'&&stop.city&&!city){stop.city='';const input=document.getElementById(`atlas-city-${stop.id}`);if(input)input.value='';stop.photo_place='';const association=document.getElementById(`atlas-place-${stop.id}`);if(association)association.value='';}
      if(city){if(!stop.country){stop.country=city.country;const select=document.getElementById(`atlas-country-${stop.id}`);if(select)select.value=stop.country;}setCoordinates(stop,city.lat,city.lon);}
      else if(!stop.city&&country)setCoordinates(stop,country.lat,country.lon);
      else{stop.lat=null;stop.lon=null;['lat','lon'].forEach(key=>{const input=document.getElementById(`atlas-${key}-${stop.id}`);if(input)input.value='';});}
      stop.name=stop.city.trim()||countryInfo(stop.country)?.name||'';
      const suggestions=document.getElementById(`atlas-suggestions-${stop.id}`);if(suggestions)suggestions.innerHTML=suggestionMarkup(stop);
    }
    if(field.dataset.atlasField==='photo_place'){
      const suggested=suggestedStops().all.find(item=>item.photo_place===stop.photo_place);
      if(suggested){stop.name=suggested.name;stop.city=suggested.city;stop.country=suggested.country;if(!stop.date)stop.date=suggested.date;if(coordinates(suggested.lat,suggested.lon))setCoordinates(stop,suggested.lat,suggested.lon);host.renderModal();focusStop(stop.id);return;}
    }
    refreshMapPreview();
  }
  document.addEventListener('input',inputChanged);
  document.addEventListener('change',inputChanged);
  document.addEventListener('submit',event=>{if(event.target.id==='atlas-editor-form'){event.preventDefault();save().catch(error=>host.toast(error.message,'error'));}});
  function onMapPointerDown(event){
    const svg=event.target.closest?.('svg.atlas-svg');
    if(!svg||drag||modal()?.busy||event.isPrimary===false||(event.button!==undefined&&event.button!==0))return;
    suppressedClick=null;
    const inverse=svg.getScreenCTM()?.inverse(),start=inverse&&screenPoint(svg,event.clientX,event.clientY,inverse);
    if(!start)return;
    const view=svg.dataset.atlasView,viewport={...viewState(view)};
    drag={svg,view,viewport,inverse,start,clientX:event.clientX,clientY:event.clientY,pointerId:event.pointerId,moved:false};
  }
  function onMapPointerMove(event){
    if(!drag||event.pointerId!==drag.pointerId)return;
    if(!drag.moved&&Math.hypot(event.clientX-drag.clientX,event.clientY-drag.clientY)<5)return;
    if(!drag.moved){
      drag.moved=true;
      drag.svg.classList.add('is-dragging');drag.svg.parentElement?.classList.add('is-dragging');
      try{drag.svg.setPointerCapture?.(drag.pointerId);}catch(_error){}
    }
    event.preventDefault();
    const point=screenPoint(drag.svg,event.clientX,event.clientY,drag.inverse);
    if(point)panView(drag.view,drag.viewport.x-(point.x-drag.start.x),drag.viewport.y-(point.y-drag.start.y));
  }
  function onMapPointerEnd(event){
    if(!drag||(event&&event.pointerId!==undefined&&event.pointerId!==drag.pointerId))return;
    const finished=drag;drag=null;
    finished.svg.classList.remove('is-dragging');finished.svg.parentElement?.classList.remove('is-dragging');
    if(finished.moved)suppressedClick={svg:finished.svg,until:Date.now()+500};
    try{if(finished.svg.hasPointerCapture?.(finished.pointerId))finished.svg.releasePointerCapture(finished.pointerId);}catch(_error){}
  }
  function onMapWheel(event){
    if(!event.ctrlKey||!event.deltaY)return;
    const svg=event.target.closest?.('svg.atlas-svg');if(!svg)return;
    event.preventDefault();
    if(modal()?.busy||drag)return;
    const anchor=screenPoint(svg,event.clientX,event.clientY);if(!anchor)return;
    const view=svg.dataset.atlasView;
    zoomView(view,Math.round((viewState(view).scale+(event.deltaY<0?ZOOM_STEP:-ZOOM_STEP))*100)/100,anchor);
  }
  function onMapClick(event){
    const clickedMap=event.target.closest?.('svg.atlas-svg');
    if(clickedMap&&event.detail!==0&&suppressedClick?.svg===clickedMap&&suppressedClick.until>=Date.now()){
      suppressedClick=null;event.preventDefault();event.stopImmediatePropagation();return;
    }
    const active=modal();if(!active?.picking||active.busy)return;
    const svg=event.target.closest?.('#atlas-editor-map svg.atlas-svg');if(!svg)return;
    event.preventDefault();event.stopImmediatePropagation();
    const position=screenPoint(svg,event.clientX,event.clientY);if(!position)return;
    const proj=projection(active.view);
    if(!proj.inside(position.x,position.y)){host.toast('请选择地图范围内的位置。','error');return;}
    const stop=active.draft.find(item=>item.id===active.selectedId);if(!stop)return;
    const coordinates=proj.invert(position.x,position.y);setCoordinates(stop,coordinates.lat,coordinates.lon);active.picking=false;host.renderModal();focusStop(stop.id);
  }
  document.addEventListener('pointerdown',onMapPointerDown);
  document.addEventListener('pointermove',onMapPointerMove,{passive:false});
  document.addEventListener('pointerup',onMapPointerEnd);
  document.addEventListener('pointercancel',onMapPointerEnd);
  document.addEventListener('lostpointercapture',onMapPointerEnd);
  document.addEventListener('wheel',onMapWheel,{passive:false});
  document.addEventListener('click',onMapClick,true);
  if(typeof ResizeObserver!=='undefined')resizeObserver=new ResizeObserver(()=>scheduleMapLayout());
  if(typeof MutationObserver!=='undefined'&&document.body)new MutationObserver(records=>{
    if(records.some(record=>[...record.addedNodes].some(node=>node.matches?.('svg.atlas-svg')||node.querySelector?.('svg.atlas-svg'))))scheduleMapLayout();
  }).observe(document.body,{childList:true,subtree:true});
  window.addEventListener?.('resize',scheduleMapLayout);
  window.addEventListener?.('blur',()=>onMapPointerEnd());
  document.addEventListener('keydown',event=>{
    const pin=event.target.closest?.('[data-atlas-pin]');
    if(pin&&(event.key==='Enter'||event.key===' ')){event.preventDefault();event.stopImmediatePropagation();handleAction('atlas-select',pin).catch(error=>host.toast(error.message,'error'));}
  });
  window.ShiguangAtlas={configure,render,renderModal,handleAction,projection,featurePath,refreshLayout:refreshMapLayouts,countries:()=>model.countries,countryForPlace,loadCountries:async()=>{await ensureLoaded();return model.countries;}};
})();
