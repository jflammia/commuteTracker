import { expect, test } from '@playwright/test';

test('labeling workbench end to end', async ({ page }) => {
  await page.goto('/trips');
  await expect(page.getByRole('heading', { name: 'Trips' })).toBeVisible();
  // the seeded commute produced exactly one trip
  await page.getByRole('link', { name: /\d/ }).first().click();
  await expect(page.getByTestId('trip-map')).toBeVisible();
  await expect(page.getByTestId('segment-panel')).toBeVisible();

  // change the first segment's mode to train
  const select = page.getByTestId(/mode-select-/).first();
  await select.selectOption('train');
  await expect(page.getByText('labeled').first()).toBeVisible();

  // mark reviewed — button text changes to "✓ Reviewed" and becomes disabled
  await page.getByTestId('mark-reviewed').click();
  await expect(page.getByTestId('mark-reviewed')).toBeDisabled();

  // list reflects review state
  await page.goto('/trips');
  await expect(page.getByText('✓ reviewed')).toBeVisible();
});
