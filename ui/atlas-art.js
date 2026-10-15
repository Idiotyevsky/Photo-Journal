/* Offline vector washes and pen drawings. Geographic overlays live in atlas.js. */
(() => {
  'use strict';
  const WIDTH=1000,HEIGHT=500;
  const COLORS=[
    ['#eee0b9','#dec58f'],['#e8d0c1','#d9b5a4'],
    ['#d7e2c7','#b7caa5'],['#f0d8b9','#dfbd91'],
    ['#ddd8e5','#c1b9ce'],['#e2e3bf','#c4cd9c'],
    ['#cde2d9','#a8c9bb'],['#d5e1e6','#afc8d2']
  ];
  const paths=new WeakMap();
  const esc=value=>String(value??'').replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
  const hash=value=>{let n=2166136261;for(const c of String(value)){n^=c.codePointAt(0);n=Math.imul(n,16777619);}return n>>>0;};
  const finite=point=>point&&Number.isFinite(point.x)&&Number.isFinite(point.y);

  function projectionKey(view,proj){
    const samples=[[-170,-50],[0,0],[90,45],[135,54]].map(([lon,lat])=>{
      const point=proj.point(lon,lat);return finite(point)?`${point.x.toFixed(5)},${point.y.toFixed(5)}`:'outside';
    });
    return `${view}|${Array.isArray(proj.extent)?proj.extent.join(','):''}|${samples.join(';')}`;
  }
  function geometryPath(geometry,proj,featurePath,key){
    if(!geometry||typeof geometry!=='object')return '';
    let entries=paths.get(geometry);
    if(!entries){entries=new Map();paths.set(geometry,entries);}
    let entry=entries.get(key);
    if(!entry||entry.featurePath!==featurePath){
      entry={featurePath,path:featurePath(geometry,proj)||''};
      if(entries.size>=4)entries.delete(entries.keys().next().value);
      entries.set(key,entry);
    }
    return entry.path;
  }
  function group(feature){
    const supplied=String(feature.group||feature.country||'').toUpperCase();
    if(['CHN','CN','CHINA'].includes(supplied))return 'CHN';
    if(['TWN','TW','TAIWAN'].includes(supplied))return 'TWN';
    if(['HKG','HK'].includes(supplied))return 'HKG';
    if(['MAC','MO'].includes(supplied))return 'MAC';
    const id=String(feature.id||'');
    return id.startsWith('PFA')?'CHN':id.slice(0,3);
  }
  function definitions(prefix){
    return `<defs><linearGradient id="${prefix}-ocean" x1="0" y1="0" x2=".2" y2="1"><stop offset="0" stop-color="#e9f3f0"/><stop offset=".55" stop-color="#deeeea"/><stop offset="1" stop-color="#d2e6e4"/></linearGradient><radialGradient id="${prefix}-sea-wash"><stop offset="0" stop-color="#fffdf0" stop-opacity=".72"/><stop offset="1" stop-color="#fffdf0" stop-opacity="0"/></radialGradient>${COLORS.map(([light,dark],index)=>`<linearGradient id="${prefix}-land-${index}" x1="0" y1="0" x2=".65" y2="1"><stop offset="0" stop-color="${light}"/><stop offset=".6" stop-color="${light}"/><stop offset="1" stop-color="${dark}"/></linearGradient>`).join('')}</defs>`;
  }
  function countries(features,view,proj,featurePath,key,prefix){
    return (Array.isArray(features)?features:[]).map(feature=>{
      if(view==='china'&&!['CHN','TWN','HKG','MAC'].includes(String(feature.id)))return '';
      const path=geometryPath(feature.geometry,proj,featurePath,key);if(!path)return '';
      const background=view==='china',reference=['CHN','TWN','HKG','MAC'].includes(String(feature.id));
      const fill=background?(reference?'#e7e5ce':'#e5e7d8'):`url(#${prefix}-land-${hash(feature.id)%COLORS.length})`;
      return `<path class="atlas-country" data-feature-id="${esc(feature.id)}" d="${esc(path)}" fill="${fill}" fill-rule="evenodd" stroke="${background?'#acb6a6':'#8c9c91'}" stroke-opacity="${background?.64:.92}" stroke-width="${background?.65:.72}" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>`;
    }).join('');
  }
  function provinces(features,proj,featurePath,key,prefix){
    return (Array.isArray(features)?features:[]).map(feature=>{
      const path=geometryPath(feature.geometry,proj,featurePath,key);if(!path)return '';
      return `<path class="atlas-province" data-feature-id="${esc(feature.id)}" data-region="${esc(group(feature))}" d="${esc(path)}" fill="url(#${prefix}-land-${hash(feature.id)%COLORS.length})" fill-rule="evenodd" stroke="#8e9d8b" stroke-opacity=".94" stroke-width=".72" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>`;
    }).join('');
  }
  function at(proj,lon,lat,markup,scale=1){
    if(typeof proj.contains==='function'&&!proj.contains(lat,lon))return '';
    const point=proj.point(lon,lat);if(!finite(point)||point.x<12||point.x>WIDTH-12||point.y<12||point.y>HEIGHT-12)return '';
    return `<g transform="translate(${point.x.toFixed(2)} ${point.y.toFixed(2)}) scale(${scale})">${markup}</g>`;
  }
  const mountain=`<path d="M-20 9-9-8 1 9M-3 9 8-13 21 9" fill="#e8e2ce" fill-opacity=".7" stroke="#8d9b86" stroke-width=".85" stroke-linejoin="round"/><path d="m-13-2 4 3 3-4M3-5l5 4 4-5M-18 12c9-1 19 1 36-1" fill="none" stroke="#a5ad94" stroke-width=".75" stroke-linecap="round"/>`;
  const trees=`<path d="M-8 10V-8M8 12V-3" fill="none" stroke="#8f9b79" stroke-width=".8"/><path d="m-15 0 7-13L-1 0h-4l7 8h-20l7-8Zm17 4 6-12 6 12h-3l5 7H0l5-7Z" fill="#afc39b" fill-opacity=".6" stroke="#879b78" stroke-width=".75" stroke-linejoin="round"/>`;
  const waves=`<path d="M-20-3c3-4 6-4 9 0s6 4 9 0 6-4 9 0 6 4 9 0M-12 5c3-3 5-3 8 0s5 3 8 0 5-3 8 0" fill="none" stroke="#87b4b7" stroke-width="1" stroke-linecap="round" opacity=".6"/>`;
  function seaDetails(view,proj){
    const locations=view==='china'?[[128,23,1.15],[119,17,1],[132,39,.85]]:[[-145,24,1.2],[-30,1,1],[73,-30,1.05],[150,-24,1.1]];
    return locations.map(([lon,lat,size])=>at(proj,lon,lat,waves,size)).join('');
  }
  function landDetails(view,proj){
    const mountains=view==='china'?[[84,32,.78],[92,33,.7],[101,36,.58]]:[[86,33,.48],[10,46,.38],[-111,43,.48],[-70,-18,.45]];
    const forests=view==='china'?[[124,46,.56],[105,29,.5]]:[[25,60,.43],[-100,55,.48],[21,-3,.4]];
    return mountains.map(([lon,lat,size])=>at(proj,lon,lat,mountain,size)).join('')+forests.map(([lon,lat,size])=>at(proj,lon,lat,trees,size)).join('');
  }
  function compass(){
    return `<g class="atlas-art-compass" transform="translate(939 69)" stroke="#8b9f98" stroke-width=".85" fill="none" opacity=".72"><circle r="20" stroke-dasharray="1 4"/><path d="M0-29V29M-29 0h58" stroke-opacity=".45"/><path d="M0-23 6 0 0 23-6 0Z" fill="#f8f7e8"/><path d="M0-23 6 0H0Z" fill="#7e9b94" stroke="none"/><circle r="2.2" fill="#f8f7e8"/></g>`;
  }
  function render(view,worldFeatures,chinaFeatures,proj,featurePath,prefix='map'){
    if(!proj||typeof proj.point!=='function'||typeof featurePath!=='function')return '';
    const id='atlas-art-'+String(prefix).replace(/[^A-Za-z0-9_-]/g,'_');
    const key=projectionKey(view,proj);
    return definitions(id)+`<g class="atlas-art" pointer-events="none" aria-hidden="true"><rect x="0" y="0" width="${WIDTH}" height="${HEIGHT}" fill="url(#${id}-ocean)"/><ellipse cx="260" cy="80" rx="440" ry="230" fill="url(#${id}-sea-wash)"/><ellipse cx="810" cy="440" rx="360" ry="210" fill="url(#${id}-sea-wash)"/>${seaDetails(view,proj)}${countries(worldFeatures,view,proj,featurePath,key,id)}${view==='china'?provinces(chinaFeatures,proj,featurePath,key,id):''}<g class="atlas-art-drawings" opacity=".78" pointer-events="none">${landDetails(view,proj)}${compass()}</g></g>`;
  }
  window.ShiguangAtlasArt={render};
})();
