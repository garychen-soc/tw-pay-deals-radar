/** web/public/data/index.json 的型別（與 src/payradar/models.py、emit.py 對應）。 */

export type Tri = "yes" | "no" | "unknown";
export type Unit = "%" | "元" | "點" | "折" | "筆" | "次" | "份" | "other";
export type Per = "txn" | "day" | "week" | "month" | "campaign" | "unknown";

export interface Evidence { quote: string; url: string }

export interface Slot {
  start: string | null;
  end: string | null;
  kind: "opens_at" | "range" | "deadline";
  origin: string;
  precision: "minute" | "day";
  holiday_shifted: boolean;
  note: string;
  evidence: Evidence;
}

export interface Reward { kind: string; value: number | null; unit: Unit; quote: string }
export interface Amount { amount: number; unit: Unit; per: Per; quote: string }

export interface SoldOut {
  scope_label: string;
  scope_start: string | null;
  scope_end: string | null;
  source: "terms" | "title";
  evidence: Evidence;
}

export interface Offer {
  name: string;
  slots: Slot[];
  rules: string[];
  reregister: "each_period" | "once" | "unknown";
  order: "register_before_spend" | "retroactive_ok" | "unknown";
  quota: number | null;
  quota_quote: string;
  review: string[];
  rewards: Reward[];
  min_spends: Amount[];
  caps: Amount[];
  new_users_only: Tri;
  where: string;
  how: string;
  limited: Tri;
  limited_quote: string;
  sold_out: SoldOut[];
}

export interface Activity {
  id: string;
  provider_id: string;
  provider_name: string;
  title: string;
  aliases: string[];
  url: string;
  period_start: string | null;
  period_end: string | null;
  category: string;
  terms_source: "official" | "external" | "image" | "none";
  external_url: string;
  is_offer: Tri;
  registration: "required" | "not_required" | "unknown";
  registration_quote: string;
  offers: Offer[];
  title_sold_out: SoldOut[];
  portal_url: string;
  review: string[];
  stale: boolean;
}

export interface SourceHealth {
  provider_id: string;
  provider_name: string;
  status: "ok" | "partial" | "failed" | "blocked";
  entry_url: string;
  stubs: number;
  activities: number;
  detail_failures: number;
  message: string;
  carried: number;
}

export interface TimelineRow {
  aid: string;
  provider_id: string;
  provider: string;
  title: string;
  sub: string;
  sub_index: number;
  start: string | null;
  end: string | null;
  kind: string;
  precision: "minute" | "day";
  shifted: boolean;
  note: string;
  quote: string;
  url: string;
  stale: boolean;
}

export interface IndexData {
  schema_version: number;
  generated_at: string;
  counts: Record<string, number>;
  sources: SourceHealth[];
  coverage_gaps: { provider: string; note: string }[];
  activities: Activity[];
  timeline: TimelineRow[];
}

export interface PttPost { board: string; title: string; url: string; push: string; date: string }
export interface PttData { fetched_at: string; boards: string[]; failures: string[]; posts: PttPost[] }
