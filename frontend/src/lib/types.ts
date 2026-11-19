export interface Health { status: string; mode: "DEMO" | "LIVE"; label: string; pinned_model: string; last_routed_via: string | null; banner: string | null;
  data_as_of: { vendor_data: { from: string; to: string; lrs: number }; middle_mile_data: { from: string; to: string; trips: number } } }

export interface Kpi { id: string; label: string; unit: "rs" | "pct"; value: number; change_pct: number | null; change_unit: "pct" | "pp" | null; good_direction: "up" | "down" | "neutral"; sparkline: number[]; note?: string }
export interface Overview { as_of: Health["data_as_of"]; quarter: string; previous_quarter: string | null; kpis: Kpi[];
  monthly_margin: { month: string; lrs: number; billed_rs: number; margin_pct: number; loss_rs: number }[];
  top_lanes_by_loss: { route: string; vehicle_type: string; lrs: number; loss_rs: number; loss_share_pct: number; margin_pct: number }[];
  alerts: { kind: "trip" | "vendor"; id: string; title: string; probability?: number; excess_rs?: number; href: string }[] }

export interface Meta { routes: { route: string; vehicle_types: string[] }[]; vehicle_types: { vehicle_type: string; capacity_mt: number }[]; vendors: { vendor_code: string; rank: number }[];
  months: { month: number; label: string }[]; route_types: string[]; zones: string[]; quarters: string[]; hubs: { source_center: string; source_name: string }[]; defaults: { qty: number; month: number; weekday: number } }

export interface Driver { feature: string; value: string | number | null; shap: number; effect: string }
export interface Booking { run_id: string; recommended_action: string; summary: string; draft: string | null; draft_template: string | null; banner: string | null; mode: string; model_used: string;
  guardrail_events: { event: string }[]; guardrail_status: "passed" | "replaced"; inputs: Record<string, string | number | null>; assumed_defaults: Record<string, string | number | null>; warnings: string[];
  loss_risk: { probability: number; flag: boolean; threshold: number; top_drivers: Driver[]; level: "low" | "medium" | "high" };
  fair_price: { fair_rs: number; fair_low_rs: number; fair_high_rs: number; quoted_rs?: number; overpay_pct?: number; excess_rs?: number };
  underload: { probability: number; flag: boolean; threshold: number; expected_utilisation_pct: number | null };
  alternatives: { vendor_code: string; rs_per_mt: number; margin_pct: number; lr_count: number }[]; current_rs_per_mt: number | null;
  policy: { signal: string; flagged: boolean; over_flag: boolean; below_flag: boolean; under_flag: boolean; cheaper_alternative: boolean; smaller_vehicle: { capacity: number } | null; lane_median_rs_per_mt: number | null } }
export interface BookingRequest { route: string; vehicle_type: string; vendor: string; qty: number; month: number; quoted_rs?: number; distance?: number }

export interface Page<T> { total: number; page: number; page_size: number; items: T[] }
export interface VendorRow { vendor_code: string; lrs: number; spend_rs: number; margin_pct: number; loss_share_pct: number; loss_rs: number; overpay_pct: number | null; excess_rs_test_quarter: number | null }
export interface VendorDetail { vendor_code: string; lrs: number; spend_rs: number; margin_pct: number; loss_share_pct: number; loss_rs: number; period: string; overpay_pct: number | null;
  worst_lanes: { route: string; vehicle_type: string; lrs: number; loss_rs: number; loss_share_pct: number }[]; monthly: { month: string; margin_pct: number; loss_rs: number; lrs: number }[] }

export interface TripRow { trip_id: string; risk: "high" | "low"; severe_probability: number; threshold: number; planned_min: number; actual_min: number; route_type: string; corridor: string; first_source_center: string; trip_creation_time: string }
export interface TripPage extends Page<TripRow> { high_risk_total: number }
export interface TripDetail { trip_id: string; route_type: string; corridor: string; planned_min: number; predicted_min: number; predicted_low_min: number; predicted_high_min: number; predicted_vs_plan_ratio: number;
  recorded_actual_min?: number; severe_delay: { probability: number; flag: boolean; threshold: number; top_drivers: Driver[] };
  legs: { leg: number; from: string; to: string; planned_min: number; actual_min: number; handling_min: number | null }[]; origin_hub: Hub | null; network_median_handling_min: number | null }
export interface Hub { source_center: string; source_name: string; legs: number; mean_actual_handling_min: number; mean_predicted_handling_min: number; excess_hours: number; excess_rs_base: number }
export interface Hubs { status: string; network_median_handling_min: number; hubs: Hub[]; rate_label: string }

export interface PromptCard { template_id: string; number: string; title: string; purpose: string; fields: { name: string; description: string }[]; template: string; example_input: string; example_output: string; failure_modes: string[] }
export interface Draft { run_id: string; kind: string; template_id: string; title: string; text: string; mode: string; banner: string | null; note: string }

export interface AuditRow { run_id: string; ts: string; page: string; mode: string; model_used: string; recommended_action: string | null; decision: "approved" | "rejected" | "edited" | null; decided_at: string | null;
  final_text: string | null; guardrail_events: string[]; banner: string | null }
export interface AuditDetail extends AuditRow { inputs: Record<string, unknown>; tools_called: { name: string; args: unknown }[]; run_text: string | null }

export interface ModelCard { task_id: string; problem: string; type: "regression" | "classification"; business_question: string; target: string; test_period: string; winner: string; winner_reason: string;
  models: { name: string; metrics: Record<string, number> }[]; headline: { metric: string; winner_value: number; baseline: { metric: string; value: number }; higher_is_better: boolean };
  top_drivers: { feature: string; plain_english: string }[]; caveats: string[]; figures: Record<string, string>; n_train: number; n_test: number }
export interface LanePoints { n_origins: number; n_destinations: number; n_lanes: number; vehicle_types: string[]; footer: string;
  origins: { name: string; lrs: number; destinations: { name: string; lrs: number }[] }[] }
export interface LaneVendor { rank: number | null; vendor_code: string; tag: "Cheapest" | "Fair" | "Costly" | null; thin: boolean; thin_reason: string | null; rs_per_mt: number; median_rs_per_shipment: number;
  gap_to_fair_pct: number | null; n_gap_shipments: number; check_data: boolean; lrs: number; shipments: number; total_mt: number; utilisation_pct: number | null; margin_pct: number; last_used: string;
  days_since_last_use: number; saving_if_switched_rs: number | null; full_year_rs_per_mt: number | null; full_year_lrs: number }
export interface LaneGroup { vehicle_type: string; basis: string; label: string; capacity_mt: number | null; lrs: number; shipments: number; n_vendors: number; n_reliable: number; thin_only: boolean;
  lane_median_rs_per_mt: number; min_rs_per_mt: number | null; max_rs_per_mt: number | null; spread_pct: number | null; vendors: LaneVendor[] }
export interface LaneRecommendation { vendor_code: string; vehicle_type: string; basis: string; capacity_mt: number | null; route: string; rs_per_mt: number; lane_median_rs_per_mt: number; lrs: number;
  last_used: string; days_since_last_use: number; qty_mt: number | null; qty_assumed: boolean; only_reliable_vendor: boolean; gap_to_fair_pct: number | null; n_gap_shipments: number;
  estimate: { fair_rs: number; fair_low_rs: number; fair_high_rs: number; vendor_gap_pct: number | null; estimate_rs: number | null; basis: string } | null; rate_based_rs: number | null;
  booking_prefill: { route: string; vehicle_type: string; vendor: string; qty: number | null; month: number; quoted: number | null } }
export interface TwoLegOption { via: string; vehicle_type: string; leg1: { from: string; to: string; vendor_code: string; rs_per_mt: number; lrs: number };
  leg2: { from: string; to: string; vendor_code: string; rs_per_mt: number; lrs: number }; total_rs_per_mt: number; direct_rs_per_mt: number | null; saving_pct: number | null; saving_rs_per_mt: number | null; label: string }
export interface LaneSearch { origin: string; destination: string; months: number; qty_mt: number | null; data_end: string; period: { from: string; to: string }; footer: string; gap_note: string; notes: string[];
  has_direct: boolean; route: string | null; distance_km: number | null; savings_total_rs: number; recommendation: LaneRecommendation | null; groups: LaneGroup[];
  summary: { lrs: number; shipments: number; vendors: number; full_year_lrs: number; margin_pct: number | null; route_codes: string[];
    focus: { vehicle_type: string; basis: string; min_rs_per_mt: number | null; max_rs_per_mt: number | null; spread_pct: number | null; n_reliable: number } } | null;
  two_leg: { label: string; has_direct: boolean; options: TwoLegOption[] } }
export interface VendorCompare { codes: string[]; months: number; n_shared_lanes: number; footer: string; notes: string[]; index_note: string; extra_cost_note: string;
  vendors: { vendor_code: string; lanes_won: number; total_mt: number; index_vs_lane_median: number | null; extra_cost_vs_cheapest_rs: number }[];
  lanes: { origin: string; destination: string; vehicle_type: string; basis: string; rs_per_mt: Record<string, number>; lrs: Record<string, number>; total_mt: Record<string, number>; cheaper: string; lane_median_rs_per_mt: number; gap_pct: number }[] }
