import { readFileSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import { notebook, uploadNotebook, zip } from './helpers.js'

test.describe.configure({ mode: 'serial' })

const READER = "def weights() -> str:\n    return open('weights.json').read()\n"
const bundle = (name, weights) => zip([{ name, data: notebook(READER) }, { name: 'weights.json', data: weights }])
const dataFile = (name) => readFileSync(join(process.env.E2E_WORK_DIR, 'uploads', `.${name}.files`, 'weights.json'), 'utf8')
const button = (page, label, name) => page.getByRole('button', { name: `${label} ${name}`, exact: true })

async function importBundle(request, name, weights, overwrite = false) {
  const response = await request.post(`/api/notebooks/import?overwrite=${overwrite}`, {
    multipart: { file: { name: 'bundle.zip', mimeType: 'application/zip', buffer: bundle(name, weights) } },
  })
  expect(response.ok()).toBeTruthy()
}

test('renaming a notebook updates the library and links its audit record', async ({ page }) => {
  await uploadNotebook(page, 'life_old.ipynb')
  await button(page, 'Rename', 'life_old.ipynb').click()
  const box = page.getByLabel('New name')
  await expect(box).toBeFocused()
  await box.fill('life_new.ipynb')
  await page.getByRole('button', { name: 'Rename', exact: true }).click()

  await expect(page.getByTestId('library-event')).toContainText('Renamed life_old.ipynb to life_new.ipynb')
  await expect(button(page, 'Rename', 'life_new.ipynb')).toBeVisible()
  await expect(button(page, 'Rename', 'life_old.ipynb')).toHaveCount(0)
  await page.getByTestId('lifecycle-audit-link').click()
  const linked = page.getByTestId('linked-record')
  await expect(linked).toContainText('Rename')
  await expect(linked).toContainText('life_new.ipynb')
})

test('renaming onto an existing name is refused and says why', async ({ page }) => {
  await uploadNotebook(page, 'life_a.ipynb')
  await uploadNotebook(page, 'life_b.ipynb')
  await button(page, 'Rename', 'life_a.ipynb').click()
  await page.getByLabel('New name').fill('life_b.ipynb')
  await page.getByRole('button', { name: 'Rename', exact: true }).click()
  await expect(page.getByTestId('note-life_a.ipynb')).toContainText(/exist|already/i)
  await expect(button(page, 'Rename', 'life_a.ipynb')).toBeVisible()
})

test('delete asks for confirmation, can be cancelled, and then removes the notebook', async ({ page, request }) => {
  await uploadNotebook(page, 'life_del.ipynb')
  const trigger = button(page, 'Delete', 'life_del.ipynb')
  await trigger.click()
  await expect(page.getByRole('alertdialog')).toContainText('restored from Trash')
  await expect(page.getByRole('button', { name: 'Cancel' })).toBeFocused()
  await page.keyboard.press('Escape')
  await expect(page.getByRole('alertdialog')).toHaveCount(0)
  await expect(trigger).toBeFocused() // focus returns to where the user was
  expect((await request.get('/api/notebooks/life_del.ipynb/versions')).status()).toBe(200)

  await trigger.click()
  await page.getByRole('button', { name: 'Yes, delete life_del.ipynb' }).click()
  await expect(page.getByTestId('library-event')).toContainText('Moved life_del.ipynb to the trash')
  await expect(button(page, 'Delete', 'life_del.ipynb')).toHaveCount(0)
  await page.getByTestId('lifecycle-audit-link').click()
  await expect(page.getByTestId('linked-record')).toContainText('Delete')
})

test('a trashed notebook comes back from the Trash with its data files', async ({ page, request }) => {
  await importBundle(request, 'life_trash.ipynb', '{"w": 7}')
  await page.goto('/')
  await button(page, 'Delete', 'life_trash.ipynb').click()
  await page.getByRole('button', { name: 'Yes, delete life_trash.ipynb' }).click()
  await expect(page.getByTestId('library-event')).toContainText('Moved life_trash.ipynb to the trash')
  await expect(button(page, 'Delete', 'life_trash.ipynb')).toHaveCount(0)
  await page.getByRole('button', { name: /^Trash \(\d+\)$/ }).click()
  await page.getByRole('button', { name: 'Restore life_trash.ipynb from the trash' }).click()
  await expect(page.getByTestId('library-event')).toContainText('Restored life_trash.ipynb from the trash')
  await expect(button(page, 'Delete', 'life_trash.ipynb')).toBeVisible()
  expect(dataFile('life_trash.ipynb')).toBe('{"w": 7}')
})

test('deleting a notebook that vanished meanwhile reports 404 and refreshes the list', async ({ page, request }) => {
  await uploadNotebook(page, 'life_gone.ipynb')
  await button(page, 'Delete', 'life_gone.ipynb').click()
  expect((await request.delete('/api/notebooks/life_gone.ipynb')).ok()).toBeTruthy() // another tab
  await page.getByRole('button', { name: 'Yes, delete life_gone.ipynb' }).click()
  await expect(page.getByTestId('library-event')).toContainText('life_gone.ipynb no longer exists')
  await expect(button(page, 'Delete', 'life_gone.ipynb')).toHaveCount(0)
})

test('version history lists earlier versions and restore brings back the notebook and its data files', async ({ page, request }) => {
  await importBundle(request, 'life_ver.ipynb', 'DATA-ONE')
  await page.goto('/')
  await button(page, 'Versions', 'life_ver.ipynb').click()
  await expect(page.getByText(/No earlier versions/)).toBeVisible()

  await importBundle(request, 'life_ver.ipynb', 'DATA-TWO', true)
  expect(dataFile('life_ver.ipynb')).toBe('DATA-TWO')
  await page.goto('/')
  await button(page, 'Versions', 'life_ver.ipynb').click()
  const versions = page.getByTestId('version-life_ver.ipynb')
  await expect(versions).toHaveCount(1)
  await expect(versions.first()).toContainText('1 data file')

  await versions.first().getByRole('button', { name: /^Restore the version of life_ver.ipynb/ }).click()
  expect(dataFile('life_ver.ipynb')).toBe('DATA-TWO') // nothing happens before confirming
  await page.getByRole('button', { name: 'Confirm restore' }).click()
  await expect(page.getByTestId('note-life_ver.ipynb')).toContainText('Restored the version saved')
  await expect(page.getByTestId('note-life_ver.ipynb')).toContainText('1 data file')
  expect(dataFile('life_ver.ipynb')).toBe('DATA-ONE')
  await page.getByTestId('lifecycle-audit-link').click()
  await expect(page.getByTestId('linked-record')).toContainText('Restore version')
  await expect(page.getByTestId('version-life_ver.ipynb')).toHaveCount(2) // the restore saved the replaced state
})
