import { getRecommendation, type OptimizerResult } from '$lib/api';
import type { PageLoad } from './$types';

export const load: PageLoad = async ({ fetch }) => {
  try {
    const rec: OptimizerResult = await getRecommendation(fetch);
    return { rec, configured: true as const };
  } catch (e) {
    if (e instanceof Error && e.message === 'optimizer-unconfigured') {
      return { rec: null, configured: false as const };
    }
    throw e;
  }
};
