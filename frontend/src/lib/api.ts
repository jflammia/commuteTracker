export interface TripSummary {
  trip_id: string;
  start_ts: string;
  end_ts: string;
  duration_s: number;
  distance_m: number;
  direction: string;
  start_geofence: string | null;
  end_geofence: string | null;
  reviewed: boolean;
}

export interface TrainInfo {
  seg_index: number;
  source: string;
  gtfs_trip_id: string;
  route_name: string;
  headsign: string;
  board_stop: string;
  alight_stop: string;
  scheduled_dep_s: number;
  delta_s: number;
}

export interface Segment {
  seg_index: number;
  mode: string;
  mode_effective: string;
  mode_source: string;
  start_ts: string;
  end_ts: string;
  duration_s: number;
  distance_m: number;
  point_count: number;
}

export interface ItineraryLeg {
  mode: string;
  start_ts: string;
  end_ts: string;
  duration_s: number;
  distance_m: number;
  train: TrainInfo | null;
  confirmation: string | null;
}

export interface TripDetail {
  trip: TripSummary & { flag: string | null };
  segments: Segment[];
  points: { ts: string; lat: number; lon: number; speed_mps: number }[];
  itinerary: ItineraryLeg[];
}

export type LabelEvent = {
  type: 'segment_mode' | 'train_match' | 'trip_flag' | 'trip_reviewed';
  trip_id: string;
  seg_index?: number;
  value: string | boolean;
};

async function check(resp: Response): Promise<Response> {
  if (!resp.ok) throw new Error(`${resp.status} ${await resp.text()}`);
  return resp;
}

export async function getTrips(
  fetchFn: typeof fetch,
  reviewed?: boolean,
): Promise<TripSummary[]> {
  const qs = reviewed === undefined ? '' : `?reviewed=${reviewed}`;
  return (await check(await fetchFn(`/api/trips${qs}`))).json();
}

export async function getTrip(fetchFn: typeof fetch, id: string): Promise<TripDetail> {
  return (await check(await fetchFn(`/api/trips/${id}`))).json();
}

export async function postLabel(label: LabelEvent): Promise<{ applied: boolean }> {
  const resp = await fetch('/api/labels', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(label),
  });
  return (await check(resp)).json();
}
