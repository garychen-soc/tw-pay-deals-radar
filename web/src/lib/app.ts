/**
 * 台灣支付優惠雷達前端。
 *
 * 資料檔只有事實；「進行中、即將開始、即將截止、高回饋、現在是否額滿」全部在這裡
 * 以台北「今天」計算 —— 資料放三天也不會顯示錯的狀態。所有文字一律走 textContent，
 * 連結只接受 https。
 */

import { addDays, dayLabel, moment, relative, taipeiDay } from "./time";
import type {
  Activity, Amount, IndexData, Offer, PttData, Reward, SoldOut, SourceHealth, TimelineRow,
} from "./types";

const BASE = (document.body.dataset.base ?? "/").replace(/\/?$/, "/");
const SITE = new URL(BASE, location.origin).href;
const ICS_URL = `${SITE}calendar/deals.ics`;
const STORE_THEME = "payradar.v2.theme";
const STALE_HOURS = 30;
const UPCOMING_DAYS = 14;
const ENDING_DAYS = 7;

type Tab = "deals" | "timeline" | "ptt" | "sources";
type Cat = "featured" | "high" | "upcoming" | "ending" | "sold" | "all";
const CATS: [Cat, string][] = [
  ["featured", "精選"], ["high", "高回饋"], ["upcoming", "即將開始"],
  ["ending", "即將截止"], ["sold", "已／部分額滿"], ["all", "全部"],
];

const state = {
  tab: "deals" as Tab,
  cat: "featured" as Cat,
  q: "",
  provider: "",
  regOnly: false,
  showNotice: false,
  data: null as IndexData | null,
  ptt: null as PttData | null,
  today: taipeiDay(new Date()),
};

// ---- 小工具 -----------------------------------------------------------------

function el<K extends keyof HTMLElementTagNameMap>(
  tag: K, cls = "", text = "",
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text) node.textContent = text;
  return node;
}

function safeUrl(href: string): string | null {
  try {
    const url = new URL(href);
    return url.protocol === "https:" ? url.href : null;
  } catch {
    return null;
  }
}

function link(href: string, text: string, cls = "btn"): HTMLElement {
  const url = safeUrl(href);
  if (!url) return el("span", "muted", text);
  const a = el("a", cls, text);
  a.href = url;
  a.target = "_blank";
  a.rel = "noopener";
  return a;
}

function load(key: string): string | null {
  try { return localStorage.getItem(key); } catch { return null; }
}

function save(key: string, value: string): void {
  try { localStorage.setItem(key, value); } catch { /* 私密模式 */ }
}

const num = new Intl.NumberFormat("zh-TW");

function amountText(value: number, unit: string): string {
  if (unit === "%") return `${value}%`;
  if (unit === "折") return `${value}折`;
  if (unit === "元") return `${num.format(value)} 元`;
  if (unit === "other") return num.format(value);
  return `${num.format(value)} ${unit}`;
}

function rewardText(r: Reward): string {
  if (r.value === null) {
    return { coupon: "優惠券", gift: "贈品", discount: "折扣", points: "點數回饋" }[r.kind] ?? "回饋";
  }
  return amountText(r.value, r.unit);
}

const PER: Record<string, string> = {
  txn: "每筆", day: "每日", week: "每週", month: "每月", campaign: "活動期間", unknown: "",
};

function capText(c: Amount): string {
  return `${PER[c.per] ?? ""}上限 ${amountText(c.amount, c.unit)}`.trim();
}

function minText(m: Amount): string {
  if (["筆", "次", "份"].includes(m.unit)) return `${PER[m.per] ?? ""}任 ${m.amount} ${m.unit}`.trim();
  const head = m.per === "txn" ? "單筆滿" : m.per === "campaign" ? "累計滿" : "滿";
  return `${head} ${amountText(m.amount, m.unit)}`;
}

// ---- 以「今天」計算的狀態 ---------------------------------------------------------

function daysUntil(day: string | null): number {
  if (!day) return Infinity;
  const ms = Date.parse(`${day}T00:00:00+08:00`) - Date.parse(`${state.today}T00:00:00+08:00`);
  return Math.round(ms / 86400000);
}

function isUpcoming(a: Activity): boolean {
  const d = daysUntil(a.period_start);
  return d >= 1 && d !== Infinity;
}

function isEnding(a: Activity): boolean {
  const d = daysUntil(a.period_end);
  return d >= 0 && d <= ENDING_DAYS;
}

function isLive(a: Activity): boolean {
  return !isUpcoming(a) && daysUntil(a.period_end) >= 0;
}

function isHigh(a: Activity): boolean {
  return a.offers.some((o) =>
    o.rewards.some((r) => r.value !== null && ((r.unit === "%" && r.value >= 10) || (r.unit === "元" && r.value >= 100))),
  );
}

type SoldScope = "now" | "past" | "future" | "unknown";

/** 一則「已額滿」聲明相對今天：涵蓋今天／已過去的期間／還沒到的期間／只有範圍字樣。 */
function soldScope(s: SoldOut): SoldScope {
  if (!s.scope_start && !s.scope_end) return s.scope_label ? "unknown" : "now";
  if (s.scope_end && s.scope_end < state.today) return "past";
  if (s.scope_start && s.scope_start > state.today) return "future";
  return "now";
}

type Sold = "full" | "partial" | "past" | "none";

function soldState(a: Activity): Sold {
  const titleNow = a.title_sold_out.filter((s) => soldScope(s) === "now");
  if (titleNow.some((s) => !s.scope_start && !s.scope_label)) return "full";
  const withRewards = a.offers.filter((o) => o.rewards.length || o.sold_out.length);
  const offerNow = (o: Offer) => o.sold_out.some((s) => soldScope(s) === "now");
  if (withRewards.length && withRewards.every(offerNow)) return "full";
  const all = [...a.title_sold_out, ...a.offers.flatMap((o) => o.sold_out)];
  if (titleNow.length || all.some((s) => ["now", "unknown"].includes(soldScope(s)))) return "partial";
  if (all.length) return "past";
  return "none";
}

function featuredScore(a: Activity): number {
  let s = 0;
  if (isHigh(a)) s += 35;
  if (isEnding(a)) s += 18;
  if (isUpcoming(a) && daysUntil(a.period_start) <= UPCOMING_DAYS) s += 20;
  if (a.registration === "required") s += 5;
  const sold = soldState(a);
  if (sold === "full") s -= 60;
  if (sold === "partial") s -= 10;
  return s;
}

// ---- 篩選 -----------------------------------------------------------------

function haystack(a: Activity): string {
  return [
    a.provider_name, a.title, ...a.aliases, a.category,
    ...a.offers.flatMap((o) => [o.name, o.where, o.how]),
  ].join(" ").toLocaleLowerCase("zh-Hant");
}

function visible(a: Activity): boolean {
  if (!state.showNotice && a.is_offer === "no") return false;
  if (daysUntil(a.period_end) < 0) return false;
  if (state.provider && a.provider_id !== state.provider) return false;
  if (state.regOnly && a.registration !== "required") return false;
  if (state.q && !haystack(a).includes(state.q.toLocaleLowerCase("zh-Hant"))) return false;
  switch (state.cat) {
    case "featured": return isLive(a) || (isUpcoming(a) && daysUntil(a.period_start) <= UPCOMING_DAYS)
      ? featuredScore(a) >= 18 : false;
    case "high": return isHigh(a) && soldState(a) !== "full";
    case "upcoming": return isUpcoming(a) && daysUntil(a.period_start) <= UPCOMING_DAYS;
    case "ending": return isEnding(a);
    case "sold": return soldState(a) === "full" || soldState(a) === "partial";
    default: return true;
  }
}

function sorter(l: Activity, r: Activity): number {
  if (state.cat === "featured" || state.cat === "high") {
    const d = featuredScore(r) - featuredScore(l);
    if (d) return d;
  }
  if (state.cat === "upcoming") return (l.period_start ?? "9") < (r.period_start ?? "9") ? -1 : 1;
  const le = l.period_end ?? "9999", re = r.period_end ?? "9999";
  if (le !== re) return le < re ? -1 : 1;
  return l.title.localeCompare(r.title, "zh-Hant");
}

// ---- 卡片 -----------------------------------------------------------------

function periodText(a: Activity): string {
  const fmt = (d: string) => `${Number(d.slice(5, 7))}/${Number(d.slice(8, 10))}`;
  const span = a.period_start && a.period_end
    ? `${fmt(a.period_start)}–${fmt(a.period_end)}`
    : a.period_end ? `至 ${fmt(a.period_end)}` : a.period_start ? `${fmt(a.period_start)} 起` : "期間未公告";
  if (isUpcoming(a)) return `${span}・${daysUntil(a.period_start)} 天後開始`;
  const left = daysUntil(a.period_end);
  if (left === 0) return `${span}・今天截止`;
  if (left <= ENDING_DAYS) return `${span}・剩 ${left} 天`;
  return span;
}

function googleUrl(a: Activity): string | null {
  if (!a.period_end) return null;
  const end = a.period_end.replaceAll("-", "");
  const next = addDays(a.period_end, 1).replaceAll("-", "");
  const params = new URLSearchParams({
    action: "TEMPLATE",
    text: `截止｜${a.provider_name}｜${a.title}`,
    dates: `${end}/${next}`,
    details: a.url,
  });
  return `https://calendar.google.com/calendar/render?${params}`;
}

function soldLine(s: SoldOut): HTMLElement {
  const scope = soldScope(s);
  const label = s.scope_label ? `（${s.scope_label}）` : "";
  const head = {
    now: `已額滿${label}`, past: `上一期已額滿${label}，本期請至官方確認`,
    future: `已額滿${label}`, unknown: `部分額滿${label}`,
  }[scope];
  const box = el("details", "evidence");
  const sum = el("summary", scope === "past" ? "muted" : "sold", head);
  box.append(sum, el("blockquote", "", s.evidence.quote));
  return box;
}

function offerBlock(o: Offer, index: number, multi: boolean): HTMLElement {
  const box = el("div", "offer");
  // 子活動名稱沒通過原文驗證時是空字串：多個方案仍要分得出來
  if (multi) box.append(el("h4", "", o.name ? o.name.replace(/^【(.*)】$/, "$1") : `方案 ${index + 1}`));
  if (o.rewards.length) {
    const chips = el("div", "rewards");
    for (const r of o.rewards) {
      const chip = el("span", "reward", rewardText(r));
      chip.title = r.quote;
      chips.append(chip);
    }
    box.append(chips);
  }
  const terms: string[] = [];
  for (const m of o.min_spends) terms.push(minText(m));
  for (const c of o.caps) terms.push(capText(c));
  if (o.new_users_only === "yes") terms.push("限新戶");
  if (o.limited === "yes") terms.push(o.quota ? `限量 ${num.format(o.quota)}` : "限量");
  if (terms.length) box.append(el("p", "terms", terms.join("・")));
  if (o.where) box.append(el("p", "note", `通路：${o.where}`));
  if (o.how) box.append(el("p", "note", `方式：${o.how}`));
  for (const s of o.sold_out) box.append(soldLine(s));
  if (o.slots.length) {
    for (const slot of o.slots) {
      const when = slot.start
        ? `${moment(slot.start, slot.precision)} 開放${slot.end ? `，至 ${moment(slot.end)}` : ""}`
        : slot.end ? `${moment(slot.end)} 前完成` : "";
      if (when) box.append(el("p", "slotline", `登錄／領券：${when}`));
    }
  } else if (o.rules.length) {
    box.append(el("p", "slotline", `登錄／領券：${o.rules[0]}`));
  }
  return box;
}

const SOLD_BADGE: Record<Sold, [string, string] | null> = {
  full: ["已額滿", "hot"], partial: ["部分額滿", "warn"], past: ["上期曾額滿", ""], none: null,
};

function activityCard(a: Activity): HTMLElement {
  const sold = soldState(a);
  const stripe = sold === "full" ? "s-crit" : isEnding(a) ? "s-warn" : isUpcoming(a) ? "s-up" : isHigh(a) ? "s-acc" : "";
  const card = el("article", `deal ${stripe}${a.stale ? " is-stale" : ""}${sold === "full" ? " is-full" : ""}`);
  const head = el("div", "d-head");
  head.append(el("span", "provider", a.provider_name), el("span", "period", periodText(a)));
  card.append(head);
  const h3 = el("h3");
  h3.append(link(a.url, a.title, "title-link"));
  card.append(h3);

  const badges = el("div", "badges");
  const add = (text: string, cls = "") => badges.append(el("span", `badge ${cls}`.trim(), text));
  const soldBadge = SOLD_BADGE[sold];
  if (soldBadge) add(...soldBadge);
  if (isHigh(a)) add("高回饋", "acc");
  if (isUpcoming(a)) add("即將開始", "up");
  if (a.registration === "required") add("需登錄／領券");
  if (a.offers.some((o) => o.new_users_only === "yes")) add("新戶");
  if (a.is_offer === "no") add("非優惠公告");
  if (a.stale) add("本輪讀取失敗，沿用上一輪", "warn");
  if (badges.childElementCount) card.append(badges);

  for (const s of a.title_sold_out) card.append(soldLine(s));
  if (a.terms_source === "external") {
    card.append(el("p", "note warn", "條款在合作方網站，未自動整理；請點活動頁確認條件。"));
  } else if (a.terms_source === "image") {
    card.append(el("p", "note warn", "官方條款是圖片，未自動整理；請點活動頁確認條件。"));
  }
  const offers = a.offers.filter((o) => o.rewards.length || o.min_spends.length || o.caps.length || o.sold_out.length || o.slots.length || o.rules.length);
  offers.forEach((o, i) => card.append(offerBlock(o, i, offers.length > 1)));

  const review = [...a.review, ...a.offers.flatMap((o) => o.review)];
  if (review.length && a.terms_source === "official") {
    const box = el("details", "evidence");
    box.append(el("summary", "muted", `${review.length} 項程式無法確認，請以官方為準`));
    const ul = el("ul", "review");
    for (const r of [...new Set(review)]) ul.append(el("li", "", r));
    box.append(ul);
    card.append(box);
  }

  const actions = el("div", "actions");
  actions.append(link(a.url, "官方活動頁", "btn primary"));
  const g = googleUrl(a);
  if (g) actions.append(link(g, "截止日加入 Google 日曆"));
  card.append(actions);
  return card;
}

// ---- 各分頁 -----------------------------------------------------------------

function renderDeals(root: HTMLElement, data: IndexData): void {
  const rows = data.activities.filter(visible).sort(sorter);
  root.append(el("p", "count muted", `${rows.length} 筆`));
  if (!rows.length) {
    const empty = el("div", "empty");
    empty.append(el("p", "", "沒有符合條件的優惠。"));
    const reset = el("button", "btn", "清除篩選");
    reset.type = "button";
    reset.addEventListener("click", () => {
      Object.assign(state, { cat: "all", q: "", provider: "", regOnly: false });
      syncControls();
      void render();
    });
    empty.append(reset);
    root.append(empty);
    return;
  }
  for (const a of rows) root.append(activityCard(a));
}

function renderTimeline(root: HTMLElement, data: IndexData): void {
  const now = new Date();
  const rows = data.timeline.filter((r) => {
    if (state.provider && r.provider_id !== state.provider) return false;
    const last = r.end ?? r.start;
    return last !== null && new Date(last) >= now;
  });
  if (!rows.length) {
    root.append(el("p", "empty", "目前沒有可落地的登錄／領券時點。多數支付優惠不需要登錄。"));
    return;
  }
  root.append(el("p", "muted", "只列條款寫明具體時點的登錄／領券；開放後先搶先贏的活動請提早。"));
  const isOpen = (r: TimelineRow) => !r.start || new Date(r.start) <= now;
  const slotCard = (r: TimelineRow, open: boolean): HTMLElement => {
    const card = el("div", `slot${r.stale ? " is-stale" : ""}`);
    const when = el("div", "when");
    if (open) {
      when.append(el("strong", "", r.end ? moment(r.end, r.precision).split(" ")[0]! : "—"));
      when.append(el("span", "tag end", r.end ? `${moment(r.end).split(" ").pop()} 截止` : "截止未公告"));
    } else {
      when.append(el("strong", "", moment(r.start!, r.precision).split(" ").pop()!));
      if (r.end) when.append(el("span", "tag end", `至 ${moment(r.end)}`));
    }
    const body = el("div", "body");
    const h3 = el("h3");
    h3.append(el("span", "bank", r.provider), link(r.url, r.title, "title-link"));
    body.append(h3);
    if (r.sub) body.append(el("p", "sub", r.sub));
    if (r.note) body.append(el("p", "note", r.note));
    const ev = el("details", "evidence");
    ev.append(el("summary", "", "官方原文"), el("blockquote", "", r.quote));
    body.append(ev);
    card.append(when, body);
    return card;
  };
  const open = rows.filter(isOpen).sort((a, b) => ((a.end ?? "9") < (b.end ?? "9") ? -1 : 1));
  if (open.length) {
    const sec = el("section", "day");
    const h2 = el("h2");
    h2.append(el("span", "day-label", "現在可登錄／領券"), el("span", "day-count", `${open.length} 個・依截止排序`));
    sec.append(h2);
    for (const r of open) sec.append(slotCard(r, true));
    root.append(sec);
  }
  const groups = new Map<string, TimelineRow[]>();
  for (const r of rows.filter((x) => !isOpen(x))) {
    const day = taipeiDay(r.start!);
    groups.set(day, [...(groups.get(day) ?? []), r]);
  }
  for (const [day, items] of [...groups].sort(([a], [b]) => (a < b ? -1 : 1))) {
    const sec = el("section", "day");
    const h2 = el("h2");
    h2.append(el("span", "day-label", `${dayLabel(day, state.today)} 開放`), el("span", "day-count", `${items.length} 個`));
    sec.append(h2);
    for (const r of items) sec.append(slotCard(r, false));
    root.append(sec);
  }
}

async function renderPtt(root: HTMLElement): Promise<void> {
  if (!state.ptt) {
    try {
      const res = await fetch(`${BASE}data/ptt.json`, { cache: "no-cache" });
      state.ptt = res.ok ? ((await res.json()) as PttData) : null;
    } catch {
      state.ptt = null;
    }
  }
  root.append(el("p", "banner", "以下是 PTT 網友貼文，未經官方確認；額滿、條件請以官方公告為準。"));
  const ptt = state.ptt;
  if (!ptt || !ptt.posts.length) {
    root.append(el("p", "empty", "目前沒有社群情報。"));
    return;
  }
  root.append(el("p", "muted", `更新於 ${relative(ptt.fetched_at)}・看板：${ptt.boards.join("、")}`));
  const ul = el("ul", "ptt");
  for (const p of ptt.posts) {
    const li = el("li");
    li.append(el("span", "push", p.push || "·"), el("span", "board", p.board), link(p.url, p.title, "title-link"));
    if (p.date) li.append(el("span", "muted", p.date));
    ul.append(li);
  }
  root.append(ul);
}

const STATUS: Record<SourceHealth["status"], string> = {
  ok: "正常", partial: "部分讀取", failed: "讀取失敗", blocked: "被擋",
};

function renderSources(root: HTMLElement, data: IndexData): void {
  const table = el("table", "sources");
  const head = el("tr");
  for (const h of ["支付服務", "狀態", "活動", "說明"]) head.append(el("th", "", h));
  table.append(head);
  for (const s of data.sources) {
    const tr = el("tr", `st-${s.status}`);
    tr.append(
      el("td", "", s.provider_name),
      el("td", "", STATUS[s.status]),
      el("td", "", String(s.activities)),
      el("td", "", [s.message, s.carried ? `沿用上一輪 ${s.carried} 筆` : "", s.detail_failures ? `明細失敗 ${s.detail_failures}` : ""].filter(Boolean).join("；")),
    );
    table.append(tr);
  }
  root.append(table);
  root.append(el("h2", "gap-title", "已知缺口"));
  const ul = el("ul", "gaps");
  for (const g of data.coverage_gaps) {
    const li = el("li");
    li.append(el("strong", "", g.provider), document.createTextNode(`：${g.note}`));
    ul.append(li);
  }
  root.append(ul);
}

// ---- 頁首 -----------------------------------------------------------------

function renderHeader(data: IndexData): void {
  const updated = document.getElementById("updated")!;
  updated.textContent = `資料更新於 ${relative(data.generated_at)}（${moment(data.generated_at)}）`;
  const hours = (Date.now() - Date.parse(data.generated_at)) / 3600000;
  const stale = document.getElementById("staleness")!;
  stale.hidden = hours < STALE_HOURS;
  stale.textContent = `資料已超過 ${Math.floor(hours)} 小時沒有更新，額滿與期間可能已變動，請以官方為準。`;
  // 與分類篩選同一套判斷，數字才會和點進去看到的筆數一致
  const offers = data.activities.filter((a) => a.is_offer !== "no" && daysUntil(a.period_end) >= 0);
  const stats: [number, string][] = [
    [offers.filter(isLive).length, "進行中優惠"],
    [offers.filter(isEnding).length, "7 天內截止"],
    [offers.filter((a) => isHigh(a) && soldState(a) !== "full").length, "高回饋"],
    [offers.filter((a) => ["full", "partial"].includes(soldState(a))).length, "已／部分額滿"],
  ];
  const box = document.getElementById("stats")!;
  box.replaceChildren(...stats.map(([n, label]) => {
    const s = el("div", "stat");
    s.append(el("strong", "", String(n)), el("span", "", label));
    return s;
  }));
}

function syncControls(): void {
  for (const b of document.querySelectorAll<HTMLButtonElement>("#cats button")) {
    b.setAttribute("aria-pressed", String(b.dataset.cat === state.cat));
  }
  (document.getElementById("q") as HTMLInputElement).value = state.q;
  (document.getElementById("provider") as HTMLSelectElement).value = state.provider;
  (document.getElementById("reg-only") as HTMLInputElement).checked = state.regOnly;
  (document.getElementById("show-notice") as HTMLInputElement).checked = state.showNotice;
  for (const b of document.querySelectorAll<HTMLButtonElement>(".tabs button")) {
    b.setAttribute("aria-selected", String(b.dataset.tab === state.tab));
  }
  const filters = document.getElementById("filters")!;
  filters.hidden = state.tab === "ptt" || state.tab === "sources";
  filters.classList.toggle("timeline-mode", state.tab === "timeline");
}

async function render(): Promise<void> {
  const root = document.getElementById("view")!;
  const data = state.data;
  if (!data) return;
  root.replaceChildren();
  if (state.tab === "deals") renderDeals(root, data);
  else if (state.tab === "timeline") renderTimeline(root, data);
  else if (state.tab === "ptt") await renderPtt(root);
  else renderSources(root, data);
}

function wire(data: IndexData): void {
  const cats = document.getElementById("cats")!;
  for (const [cat, label] of CATS) {
    const b = el("button", "chip", label);
    b.type = "button";
    b.dataset.cat = cat;
    b.addEventListener("click", () => { state.cat = cat; syncControls(); void render(); });
    cats.append(b);
  }
  const select = document.getElementById("provider") as HTMLSelectElement;
  const names = new Map(data.sources.map((s) => [s.provider_id, s.provider_name]));
  for (const [id, name] of names) {
    const opt = el("option", "", name);
    opt.value = id;
    select.append(opt);
  }
  select.addEventListener("change", () => { state.provider = select.value; void render(); });
  const q = document.getElementById("q") as HTMLInputElement;
  q.addEventListener("input", () => { state.q = q.value.trim(); void render(); });
  const reg = document.getElementById("reg-only") as HTMLInputElement;
  reg.addEventListener("change", () => { state.regOnly = reg.checked; void render(); });
  const notice = document.getElementById("show-notice") as HTMLInputElement;
  notice.addEventListener("change", () => { state.showNotice = notice.checked; void render(); });
  for (const b of document.querySelectorAll<HTMLButtonElement>(".tabs button")) {
    b.addEventListener("click", () => { state.tab = b.dataset.tab as Tab; syncControls(); void render(); });
  }

  (document.getElementById("sub-apple") as HTMLAnchorElement).href = ICS_URL.replace(/^https?:/, "webcal:");
  (document.getElementById("sub-google") as HTMLAnchorElement).href =
    `https://calendar.google.com/calendar/render?cid=${encodeURIComponent(ICS_URL.replace(/^https?:/, "webcal:"))}`;
  const url = document.getElementById("sub-url") as HTMLInputElement;
  url.value = ICS_URL;
  document.getElementById("sub-copy")!.addEventListener("click", () => {
    void navigator.clipboard?.writeText(ICS_URL);
    url.select();
  });

  document.getElementById("theme")!.addEventListener("click", () => {
    const dark = document.documentElement.dataset.theme
      ? document.documentElement.dataset.theme === "dark"
      : matchMedia("(prefers-color-scheme: dark)").matches;
    const next = dark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    save(STORE_THEME, next);
  });
}

async function boot(): Promise<void> {
  const root = document.getElementById("view")!;
  try {
    const res = await fetch(`${BASE}data/index.json`, { cache: "no-cache" });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.data = (await res.json()) as IndexData;
  } catch (err) {
    root.replaceChildren(el("p", "banner", `資料載入失敗（${String(err)}），請稍後重新整理。`));
    return;
  }
  if (load(STORE_THEME) === null) delete document.documentElement.dataset.theme;
  wire(state.data);
  renderHeader(state.data);
  syncControls();
  await render();
}

void boot();
