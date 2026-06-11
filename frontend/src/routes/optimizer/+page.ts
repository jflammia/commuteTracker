import type { PageLoad } from './$types';

export const load: PageLoad = async () => {
  const today = new Date().toISOString().slice(0, 10);
  return { today };
};
