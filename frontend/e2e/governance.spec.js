import { expect, test } from '@playwright/test'
import { uploadNotebook } from './helpers.js'

test.describe.configure({ mode: 'serial' })

test('compiling a notebook leaves an audit record the user can open and verify', async ({ page }) => {
  await uploadNotebook(page, 'e2e_ok.ipynb')
  await page.getByText(/Compile Now/).click()
  await expect(page.getByText('Compilation successful')).toBeVisible()

  const link = page.getByTestId('audit-link')
  await expect(link).toHaveText(/View audit record #\d+/)
  const sequence = (await link.textContent()).match(/#(\d+)/)[1]

  // The dashboard lists the record with its notebook, operation and outcome.
  const row = page.getByTestId(`audit-record-${sequence}`).first()
  await expect(row).toContainText('Compile')
  await expect(row).toContainText('e2e_ok.ipynb')
  await expect(row.getByTestId('audit-outcome')).toHaveText(/success/)

  // Opening the link shows that exact record.
  await link.click()
  await expect(page.getByTestId('linked-record')).toContainText(`#${sequence}`)

  // Verification is visible and meaningful.
  await page.getByText('Verify audit chain').click()
  await expect(page.getByTestId('verification')).toContainText(/Chain intact: \d+ records/)
})

test('a failed compile is recorded as a failure and linked', async ({ page, request }) => {
  await uploadNotebook(page, 'e2e_gone.ipynb')
  // The notebook disappears between upload and compile (e.g. deleted from another tab).
  const removed = await request.delete('/api/notebooks/e2e_gone.ipynb')
  expect(removed.ok()).toBeTruthy()

  await page.getByText(/Compile Now/).click()
  await expect(page.getByText(/Compilation failed: /)).toBeVisible()
  const link = page.getByTestId('audit-link')
  await expect(link).toBeVisible()
  await link.click()
  const linked = page.getByTestId('linked-record')
  await expect(linked).toContainText('Compile')
  await expect(linked).toContainText('e2e_gone.ipynb')
  await expect(linked.getByTestId('audit-outcome')).toHaveText(/failure/)

  // Filtering to failures shows it; filtering to successes does not.
  await page.getByTestId('governance-audit').getByLabel('Outcome').selectOption('failure')
  await page.getByText('Apply filters').click()
  await expect(page.getByTestId('governance-audit').getByText('e2e_gone.ipynb').first()).toBeVisible()
  await page.getByTestId('governance-audit').getByLabel('Notebook').fill('e2e_gone.ipynb')
  await page.getByTestId('governance-audit').getByLabel('Outcome').selectOption('success')
  // (the notebook's own removal above is a successful "Delete" record, so narrow to compiles)
  await page.getByTestId('governance-audit').getByLabel('Operation').selectOption('notebook.compile')
  await page.getByText('Apply filters').click()
  await expect(page.getByText(/No notebook operation records match/)).toBeVisible()
})

test('audit records survive a page reload and verification still passes', async ({ page }) => {
  await page.goto('/')
  await expect(page.getByTestId('governance-audit').getByText('e2e_ok.ipynb').first()).toBeVisible()
  await page.getByText('Verify audit chain').click()
  await expect(page.getByTestId('verification')).toContainText(/Chain intact/)
})
