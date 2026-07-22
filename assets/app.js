(() => {
  "use strict";
  const DATA_URL = "./data/promotions.json";
  const UPCOMING_DAYS = 14, ENDING_DAYS = 7, DAY_MS = 86400000;

  const catLabels = { featured:"重點", "high-return":"高回饋", upcoming:"即將開始", ending:"即將截止", "sold-out":"已額滿", all:"全部" };
  const lifecycleLabels = {
    active:{label:"進行中",cls:"is-active"}, upcoming:{label:"即將開始",cls:"is-upcoming"},
    ended:{label:"已結束",cls:""}, unknown:{label:"期間待確認",cls:""}
  };
  const quotaLabels = {
    sold_out:{label:"已額滿",cls:"is-sold"}, partial_sold_out:{label:"部分額滿",cls:"is-partial"},
    unknown_app_only:{label:"請至 App 確認",cls:"is-app"}, not_marked_full:{label:"官網未標額滿",cls:"is-open"},
    confirmed_available:{label:"尚有名額",cls:"is-open"}, unknown:{label:"名額待確認",cls:""}
  };
  const stripeByStatus = (a) => {
    const q = norm(a.quota_status);
    if (q === "sold_out" || q === "partial_sold_out") return "s-crit";
    if (isEnding(a)) return "s-warn";
    if (isUpcoming(a)) return "s-up";
    if (isHighReturn(a)) return "s-acc";
    return "s-good";
  };

  const state = { activities:[], featured:new Set(), category:"featured", query:"", provider:"" };
  const $ = (s) => document.querySelector(s);
  const el = {
    health:$("#source-health"), healthText:$("#source-health-text"), updated:$("#updated-at"),
    headline:$("#daily-headline"), summary:$("#summary-list"), search:$("#search-input"),
    provider:$("#provider-select"), tabs:$("#category-tabs"), clear:$("#clear-filters"),
    count:$("#results-count"), list:$("#activity-list"), empty:$("#empty-state"),
    emptyClear:$("#empty-clear"), error:$("#error-state"), errorMsg:$("#error-message"),
    retry:$("#retry-button"), tpl:$("#card-tpl"), pttSection:$("#ptt-section"), pttList:$("#ptt-list")
  };

  const dtf = new Intl.DateTimeFormat("zh-TW",{month:"2-digit",day:"2-digit",timeZone:"Asia/Taipei"});
  const dttf = new Intl.DateTimeFormat("zh-TW",{month:"2-digit",day:"2-digit",hour:"2-digit",minute:"2-digit",hour12:false,timeZone:"Asia/Taipei"});
  const collator = new Intl.Collator("zh-Hant",{numeric:true,sensitivity:"base"});

  function norm(v){ return String(v ?? "").trim().toLowerCase(); }
  function parseDate(v){ if(!v) return null; const t=String(v).trim();
    const d=new Date(/^\d{4}-\d{2}-\d{2}$/.test(t) ? `${t}T00:00:00+08:00` : t);
    return Number.isNaN(d.getTime()) ? null : d; }
  function startOfToday(){
    const p=new Intl.DateTimeFormat("en-CA",{timeZone:"Asia/Taipei",year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date());
    return new Date(`${p}T00:00:00+08:00`); }
  function daysFromToday(v){ const d=parseDate(v); return d ? Math.ceil((d.getTime()-startOfToday().getTime())/DAY_MS) : Infinity; }
  function fmtDate(v){ const d=parseDate(v); return d ? dtf.format(d) : "待確認"; }
  function formatPeriod(a){
    if(a.start_date && a.end_date){ return a.start_date===a.end_date ? fmtDate(a.start_date) : `${fmtDate(a.start_date)}–${fmtDate(a.end_date)}`; }
    if(a.end_date) return `～${fmtDate(a.end_date)}`;
    if(a.start_date) return `${fmtDate(a.start_date)} 起`;
    return "期間待確認"; }

  function searchText(a){ return [a.provider_name,a.title,a.channel,...(a.insights||[])].filter(Boolean).join(" ").toLocaleLowerCase("zh-Hant"); }
  function isHighReturn(a){ if(a.is_high_return===true) return true;
    const t=searchText(a); if(t.includes("高回饋")) return true;
    return [...t.matchAll(/(\d+(?:\.\d+)?)\s*%/g)].some(m=>Number(m[1])>=10); }
  function isUpcoming(a){ if(norm(a.lifecycle)==="upcoming") return true; const d=daysFromToday(a.start_date); return d>=1 && d<=UPCOMING_DAYS; }
  function isEnding(a){ if(norm(a.lifecycle)==="ended") return false; const d=daysFromToday(a.end_date); return d>=0 && d<=ENDING_DAYS; }
  function isSoldOut(a){ return ["sold_out","partial_sold_out"].includes(norm(a.quota_status)); }
  function isFeatured(a){ if(state.featured.size) return refs(a).some(r=>state.featured.has(r));
    return isHighReturn(a)||isUpcoming(a)||isEnding(a)||norm(a.quota_status)==="partial_sold_out"; }
  function refs(a){ return [a.id,a.title,a.url].map(norm).filter(Boolean); }

  function matchesCategory(a){ switch(state.category){
    case "high-return": return isHighReturn(a); case "upcoming": return isUpcoming(a);
    case "ending": return isEnding(a); case "sold-out": return isSoldOut(a);
    case "all": return true; default: return isFeatured(a); } }
  function score(a){ let s=0; if(state.featured.size&&refs(a).some(r=>state.featured.has(r))) s+=60;
    if(isHighReturn(a)) s+=35; if(isUpcoming(a)) s+=22; if(isEnding(a)) s+=18;
    if(norm(a.quota_status)==="partial_sold_out") s+=10; if(norm(a.quota_status)==="sold_out") s-=25; return s; }
  function sortActivities(l,r){
    if(state.category==="featured"||state.category==="high-return"){ const d=score(r)-score(l); if(d) return d; }
    if(state.category==="ending"){ const le=parseDate(l.end_date)?.getTime()??1e15, re=parseDate(r.end_date)?.getTime()??1e15; if(le!==re) return le-re; }
    const ls=parseDate(l.start_date)?.getTime()??1e15, rs=parseDate(r.start_date)?.getTime()??1e15;
    if(ls!==rs) return ls-rs; return collator.compare(String(l.title??""),String(r.title??"")); }
  function filtered(){ const q=state.query.toLocaleLowerCase("zh-Hant");
    return state.activities.filter(a=>{
      if(state.provider && String(a.provider_name??"")!==state.provider) return false;
      if(q && !searchText(a).includes(q)) return false;
      return matchesCategory(a); }).sort(sortActivities); }

  function safeExternalUrl(v){ try{ const u=new URL(String(v)); return ["https:","http:"].includes(u.protocol)?u.href:""; }catch{ return ""; } }
  function pad(n){ return (n<10?"0":"")+n; }
  function ymd(d){ return ""+d.getFullYear()+pad(d.getMonth()+1)+pad(d.getDate()); }
  function buildCalendarUrl(a){
    // 僅在有結束日時生成；起始日缺就用今天，涵蓋「至截止」的可用期間
    const end=parseDate(a.end_date); if(!end) return "";
    let start=parseDate(a.start_date) || startOfToday(); if(start>end) start=end;
    const endExcl=new Date(end.getTime()); endExcl.setDate(endExcl.getDate()+1);
    const title=`[${a.provider_name||"優惠"}] ${a.title||""}`.trim();
    const det=`優惠活動詳情：${a.url||""}\n（台灣行動支付優惠雷達整理，實際依官方活動辦法為準）`;
    return `https://calendar.google.com/calendar/render?action=TEMPLATE&text=${encodeURIComponent(title)}&dates=${ymd(start)}/${ymd(endExcl)}&details=${encodeURIComponent(det)}`; }

  function badge(status,map){ const c=map[norm(status)]||map.unknown; const b=document.createElement("span");
    b.className=`badge ${c.cls}`.trim(); b.textContent=c.label; return b; }

  function renderActivity(a){
    const f=el.tpl.content.cloneNode(true);
    const card=f.querySelector(".deal");
    card.classList.add(stripeByStatus(a));
    if(norm(a.quota_status)==="sold_out") card.classList.add("is-sold-out");
    f.querySelector(".svc").textContent=a.provider_name||"支付服務";
    const badges=f.querySelector(".badges");
    badges.append(badge(a.lifecycle,lifecycleLabels), badge(a.quota_status,quotaLabels));
    const title=f.querySelector(".deal__title");
    title.textContent=a.title||"未命名活動";
    const url=safeExternalUrl(a.url);
    if(url){ title.href=url; title.setAttribute("aria-label",`${a.title}－查看官方活動頁（另開新視窗）`); }
    else { title.removeAttribute("href"); }
    f.querySelector(".deal__meta").textContent=[a.channel,formatPeriod(a)].filter(Boolean).join(" · ");
    const chips=f.querySelector(".chips");
    (a.insights||[]).slice(0,3).forEach(t=>{ const li=document.createElement("li"); li.textContent=t; chips.append(li); });
    f.querySelector(".reward").textContent=a.reward||"";
    const cal=f.querySelector(".cal"), calUrl=buildCalendarUrl(a);
    if(calUrl){ cal.hidden=false;
      const go=(e)=>{ e.preventDefault(); e.stopPropagation(); window.open(calUrl,"_blank","noopener"); };
      cal.addEventListener("click",go);
      cal.addEventListener("keydown",(e)=>{ if(e.key==="Enter"||e.key===" ") go(e); }); }
    return f; }

  function render(){
    const items=filtered(), frag=document.createDocumentFragment();
    items.forEach(a=>frag.append(renderActivity(a)));
    el.list.replaceChildren(frag);
    el.list.setAttribute("aria-busy","false");
    el.count.textContent=`${catLabels[state.category]}・共 ${items.length} 項`;
    el.list.hidden=items.length===0; el.empty.hidden=items.length!==0; el.error.hidden=true;
    el.tabs.querySelectorAll("button[data-category]").forEach(b=>b.setAttribute("aria-pressed",String(b.dataset.category===state.category)));
    el.clear.hidden=!state.query && !state.provider && state.category==="featured"; }

  function renderSummary(){
    const live=state.activities.filter(a=>norm(a.lifecycle)!=="ended");
    const vals=[live.length, live.filter(isHighReturn).length, live.filter(isUpcoming).length, live.filter(isSoldOut).length];
    el.summary.querySelectorAll(".tile dd").forEach((dd,i)=>{ dd.textContent=String(vals[i]??0); }); }

  function populateProviders(){
    const seen=new Set(), provs=[];
    state.activities.forEach(a=>{ const p=String(a.provider_name||"").trim(); if(p&&!seen.has(p)){ seen.add(p); provs.push(p); } });
    provs.sort(collator.compare);
    const def=el.provider.querySelector("option[value='']"); el.provider.replaceChildren(def);
    const frag=document.createDocumentFragment();
    provs.forEach(p=>{ const o=document.createElement("option"); o.value=p; o.textContent=p; frag.append(o); });
    el.provider.append(frag); }

  function renderPtt(posts){
    if(!Array.isArray(posts)||!posts.length){ el.pttSection.hidden=true; return; }
    const frag=document.createDocumentFragment();
    posts.forEach(p=>{ const url=safeExternalUrl(p.url); if(!url) return;
      const a=document.createElement("a"); a.className="pl"; a.href=url; a.target="_blank"; a.rel="noopener";
      const bd=document.createElement("span"); bd.className="bd"; bd.textContent=p.board||"PTT";
      const pt=document.createElement("span"); pt.className="pt"; pt.textContent=p.title||"";
      const psh=document.createElement("span"); psh.className="psh"; psh.textContent=p.push||"";
      a.append(bd,pt,psh); frag.append(a); });
    el.pttList.replaceChildren(frag); el.pttSection.hidden=false; }

  function summarizeHealth(sh){
    const appOnly=state.activities.filter(a=>norm(a.quota_status)==="unknown_app_only").length;
    let status="ok", fails=0;
    if(typeof sh==="string") status=norm(sh);
    else if(sh&&typeof sh==="object"){ status=norm(sh.status||"ok"); fails=Number(sh.failures?.length ?? sh.failed ?? 0)||0; }
    el.health.classList.remove("is-warning","is-error");
    if(["failed","error","unavailable"].includes(status)){ el.health.classList.add("is-error"); el.healthText.textContent="資料更新異常"; }
    else if(fails>0||["partial","warning"].includes(status)){ el.health.classList.add("is-warning"); el.healthText.textContent="部分官網需補查"; }
    else if(appOnly>0){ el.health.classList.add("is-warning"); el.healthText.textContent=`資料更新正常・${appOnly} 項需至 App 確認`; }
    else el.healthText.textContent="資料更新正常"; }

  function renderUpdated(v){ const d=parseDate(v); el.updated.textContent = d ? `更新：${dttf.format(d)}` : "每日更新"; }

  function reset(showAll){ state.query=""; state.provider=""; state.category=showAll?"all":"featured";
    el.search.value=""; el.provider.value=""; render(); }

  async function load(){
    el.list.hidden=false; el.list.setAttribute("aria-busy","true"); el.empty.hidden=true; el.error.hidden=true; el.count.textContent="載入中…";
    try{
      const res=await fetch(DATA_URL,{cache:"no-store"});
      if(!res.ok) throw new Error(`HTTP ${res.status}`);
      const data=await res.json();
      if(!data||!Array.isArray(data.activities)) throw new Error("資料格式不正確");
      state.activities=data.activities.filter(a=>a&&typeof a==="object");
      state.featured=new Set((data.featured_ids||[]).map(norm));
      el.headline.textContent=data.headline||"今天值得留意的支付優惠";
      renderUpdated(data.generated_at);
      summarizeHealth(data.source_health);
      populateProviders(); renderSummary(); render(); renderPtt(data.ptt_posts);
    }catch(err){
      el.list.replaceChildren(); el.list.hidden=true; el.list.setAttribute("aria-busy","false");
      el.error.hidden=false; el.count.textContent="載入失敗";
      el.errorMsg.textContent="優惠資料暫時無法取得，請重新載入或稍後再試。";
      console.error("load promotions failed",err); } }

  el.search.addEventListener("input",(e)=>{ state.query=e.currentTarget.value.trim(); render(); });
  el.provider.addEventListener("change",(e)=>{ state.provider=e.currentTarget.value; render(); });
  el.tabs.addEventListener("click",(e)=>{ const b=e.target.closest("button[data-category]"); if(!b) return; state.category=b.dataset.category; render(); });
  el.clear.addEventListener("click",()=>reset(false));
  el.emptyClear.addEventListener("click",()=>reset(true));
  el.retry.addEventListener("click",load);

  // 主題切換（記憶偏好）
  (function(){ const KEY="twpay-theme", root=document.documentElement, btn=document.getElementById("themebtn");
    const cur=()=>root.getAttribute("data-theme")||(matchMedia("(prefers-color-scheme:dark)").matches?"dark":"light");
    const apply=(t)=>{ if(t) root.setAttribute("data-theme",t); btn.textContent=cur()==="dark"?"☀":"☾"; };
    try{ const s=localStorage.getItem(KEY); apply(s||undefined); }catch{ apply(); }
    btn.addEventListener("click",()=>{ const n=cur()==="dark"?"light":"dark"; apply(n); try{ localStorage.setItem(KEY,n); }catch{} }); })();

  load();
})();
