/* A quiet automatic opening, with one reflection per local calendar day. */
(() => {
  'use strict';
  const MINIMUM=2600,MAXIMUM=6500,FADE=380;
  const weekdays=['星期日','星期一','星期二','星期三','星期四','星期五','星期六'];
  const esc=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let host=null,started=false,finished=false,closing=false,startedAt=0,dismissTimer=null,dayKey='';
  function forDate(date=new Date()){
    const y=date.getFullYear(),m=date.getMonth(),d=date.getDate(),quotes=window.ShiguangDailyQuotes||['慢一点，也是在抵达。'];
    // A civil-day index avoids daylight-saving and changing localhost ports.
    const index=((Math.floor(Date.UTC(y,m,d)/86400000)%quotes.length)+quotes.length)%quotes.length;
    return {key:`${y}-${String(m+1).padStart(2,'0')}-${String(d).padStart(2,'0')}`,date:`${y}.${String(m+1).padStart(2,'0')}.${String(d).padStart(2,'0')}`,weekday:weekdays[date.getDay()],quote:quotes[index]};
  }
  function renderCorner(){
    const daily=forDate();
    return `<section class="daily-note" aria-label="今日一语"><time datetime="${daily.key}">${daily.date}<span>${daily.weekday}</span></time><p>${esc(daily.quote)}</p></section>`;
  }
  function updateDay(){
    const daily=forDate();if(daily.key===dayKey)return;dayKey=daily.key;
    document.querySelectorAll('.daily-note').forEach(note=>{const fragment=document.createElement('div');fragment.innerHTML=renderCorner();note.innerHTML=fragment.firstElementChild.innerHTML;});
    const quote=document.getElementById('daily-opening-quote'),date=document.getElementById('daily-opening-date');
    if(quote)quote.textContent=daily.quote;
    if(date){date.dateTime=daily.key;date.textContent=`${daily.date} · ${daily.weekday}`;}
  }
  function finish(){
    if(finished||closing)return;closing=true;clearTimeout(dismissTimer);
    const root=document.getElementById('daily-opening'),app=document.getElementById('app');
    document.body.classList.remove('daily-opening');
    if(app)app.inert=Boolean(host?.hasModal());
    root?.classList.add('is-leaving');
    setTimeout(()=>{root?.remove();finished=true;closing=false;},FADE);
  }
  function ready(){
    if(!started||finished||closing)return;
    clearTimeout(dismissTimer);
    dismissTimer=setTimeout(finish,Math.max(0,MINIMUM-(performance.now()-startedAt)));
  }
  function configure(context){
    if(started)return;host=context;started=true;startedAt=performance.now();
    const root=document.getElementById('daily-opening'),app=document.getElementById('app');
    if(!root){document.body.classList.remove('daily-opening');finished=true;return;}
    root.innerHTML=`<div class="daily-opening-content"><div class="daily-opening-mark" aria-hidden="true">${host.brandMark()}</div><h1 id="daily-opening-quote"></h1><time id="daily-opening-date"></time></div><div class="daily-opening-loading" role="status" aria-label="正在打开相册"><span class="daily-loading-line" aria-hidden="true"></span><span>正在翻开相册</span></div>`;
    if(app)app.inert=true;
    updateDay();dismissTimer=setTimeout(finish,MAXIMUM);
    setInterval(updateDay,30000);
    document.addEventListener('visibilitychange',()=>{if(!document.hidden)updateDay();});
    window.addEventListener('focus',updateDay);
  }
  window.ShiguangDaily={configure,ready,renderCorner,forDate};
})();
