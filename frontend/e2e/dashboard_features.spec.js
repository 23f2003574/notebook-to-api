import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import { notebook, uploadNotebook } from './helpers.js'

// Validation, deployment previews, version actions and the storage panel against the real
// dashboard, not mocked fetches: each step reads what the backend actually returned.
test.describe.configure({ mode: 'serial' })

const NAME = 'feat_e2e.ipynb'
const versions = (page) => page.getByRole('region', { name: `Versions of ${NAME}` })

test('validate, preview deployment files and read storage for an uploaded notebook', async ({ page, request }) => {
  await uploadNotebook(page, NAME)

  await page.getByRole('button', { name: `Validate ${NAME}`, exact: true }).click()
  const validate = page.getByRole('region', { name: `Validate ${NAME}` })
  await validate.getByRole('button', { name: 'Check before compiling' }).click()
  await expect(validate.getByRole('status').first()).toContainText('Would compile cleanly')
  const direct = await request.post('/api/validate', { data: { notebook_path: NAME } })
  expect(direct.ok()).toBeTruthy()

  await page.getByRole('button', { name: 'Preview deployment files' }).click()
  const panel = page.getByRole('tabpanel')
  await expect(page.getByLabel('Dockerfile contents')).toContainText('FROM python')
  await page.getByRole('tab', { name: 'Docker Compose' }).click()
  await expect(page.getByLabel('docker-compose.yml contents')).toContainText('services:')
  await page.getByRole('tab', { name: 'Kubernetes' }).click()
  await expect(page.getByLabel('kubernetes.yaml contents')).toContainText('kind: Deployment')
  await expect(panel).toBeVisible()

  await page.getByRole('button', { name: 'Show storage usage' }).click()
  const usage = page.getByTestId('storage-usage')
  await expect(usage).toContainText('Free disk:')
  await expect(usage).toContainText(/\d+ versions/)
  const storage = await (await request.get('/api/notebooks/storage')).json()
  expect(storage.notebook_count).toBeGreaterThanOrEqual(1)
  expect(typeof storage.min_free_disk_bytes).toBe('number')
})

test('edit a version note, export the history, import it again, then delete the version', async ({ page, request }) => {
  const overwrite = await request.post('/api/upload?overwrite=true', {
    multipart: { file: { name: NAME, mimeType: 'application/json', buffer: Buffer.from(notebook('def sub(a: int, b: int) -> int:\n    return a - b\n')) } },
  })
  expect(overwrite.ok()).toBeTruthy()

  await page.goto('/')
  await page.getByRole('button', { name: `Versions ${NAME}`, exact: true }).click()
  await expect(page.getByTestId(`version-${NAME}`)).toHaveCount(1)

  await versions(page).getByRole('button', { name: /^Edit the note of the version saved/ }).click()
  await versions(page).getByRole('textbox', { name: /^Note for the version saved/ }).fill('before the subtract change')
  await versions(page).getByRole('button', { name: 'Save note' }).click()
  await expect(page.getByTestId('version-note')).toContainText('before the subtract change')
  const listed = await (await request.get(`/api/notebooks/${NAME}/versions`)).json()
  const noteReply = await request.get(`/api/notebooks/${NAME}/versions/${listed.versions[0].version_id}/note`)
  expect(JSON.stringify(await noteReply.json())).toContain('before the subtract change')

  await versions(page).getByRole('button', { name: /^Copy the version saved .* to a new notebook$/ }).click()
  await versions(page).getByRole('textbox', { name: /^Name of the new notebook/ }).fill('feat_copy.ipynb')
  await versions(page).getByRole('button', { name: 'Copy version' }).click()
  await expect(page.getByRole('button', { name: 'Rename feat_copy.ipynb', exact: true })).toBeVisible()
  expect((await request.get('/api/notebooks/feat_copy.ipynb/versions')).ok()).toBeTruthy()

  const [download] = await Promise.all([
    page.waitForEvent('download'),
    versions(page).getByRole('button', { name: 'Export versions (.zip)' }).click(),
  ])
  expect(download.suggestedFilename()).toBe('feat_e2e-versions.zip')
  const archive = join(process.env.E2E_WORK_DIR, download.suggestedFilename())
  await download.saveAs(archive)
  await expect(versions(page).getByRole('status')).toContainText('Exported')

  await versions(page).locator('input[type=file]').setInputFiles(archive)
  await versions(page).getByRole('button', { name: 'Import feat_e2e-versions.zip' }).click()
  await versions(page).getByRole('button', { name: 'Yes, import' }).click()
  await expect(page.getByTestId('version-note')).toContainText('before the subtract change')

  // The import itself saved the replaced notebook as a version, so pick the annotated one.
  const annotated = page.getByTestId(`version-${NAME}`).filter({ hasText: 'before the subtract change' })
  await annotated.getByRole('button', { name: /^Delete the version saved/ }).click()
  await annotated.getByRole('button', { name: 'Yes, delete version' }).click()
  await expect(annotated).toHaveCount(0)
  const after = await (await request.get(`/api/notebooks/${NAME}/versions`)).json()
  for (const version of after.versions) {
    const reply = await request.get(`/api/notebooks/${NAME}/versions/${version.version_id}/note`)
    expect(JSON.stringify(await reply.json())).not.toContain('before the subtract change')
  }

  for (const name of [NAME, 'feat_copy.ipynb']) {
    const cleanup = await request.delete(`/api/notebooks/${name}?confirm=true`)
    expect(cleanup.status()).toBeLessThan(300)
  }
})
