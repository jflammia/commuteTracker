import { getTrip } from '$lib/api';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch, params }) => {
  return { detail: await getTrip(fetch, params.id) };
};
