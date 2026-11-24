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

test('two versions can be compared and the data files listed from the dashboard', async ({ page, request }) => {
  await importBundle(request, 'life_cmp.ipynb', 'ONE')
  const second = zip([{ name: 'life_cmp.ipynb', data: notebook(READER + '\ndef extra() -> int:\n    return 1\n') }, { name: 'weights.json', data: 'TWO' }])
  const response = await request.post('/api/notebooks/import?overwrite=true', {
    multipart: { file: { name: 'bundle.zip', mimeType: 'application/zip', buffer: second } },
  })
  expect(response.ok()).toBeTruthy()
  await page.goto('/')
  await button(page, 'Versions', 'life_cmp.ipynb').click()
  const panel = page.getByTestId('compare-life_cmp.ipynb')
  await panel.getByRole('button', { name: 'Compare versions' }).click()
  await expect(page.getByRole('region', { name: 'Added functions' })).toContainText('extra() -> int')
  await expect(page.getByRole('region', { name: 'Changed data files' })).toContainText('weights.json')
  await panel.getByRole('button', { name: 'Show data files' }).click()
  await expect(page.getByRole('list', { name: 'Data files' })).toContainText('weights.json')
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

test('the dashboard shows the key scope and disables what a read-only key cannot do', async ({ page, request }) => {
  const local = await (await request.get('/api/whoami')).json()
  expect(local).toMatchObject({ scope: 'admin', authenticated: false })
  expect(local.can.delete).toBe(true)

  await uploadNotebook(page, 'scope_nb.ipynb')
  const readOnly = { name: 'viewer', scope: 'read', authenticated: true, can: { read: true, compile: false, deploy: false, delete: false, rename: false, restore: false } }
  await page.route('**/api/whoami', (route) => route.fulfill({ json: readOnly }))
  await page.reload()
  await expect(page.getByTestId('caller')).toHaveText('Signed in as viewer (read access)')
  await expect(button(page, 'Delete', 'scope_nb.ipynb')).toBeDisabled()
  await expect(button(page, 'Rename', 'scope_nb.ipynb')).toBeDisabled()
  await expect(button(page, 'Versions', 'scope_nb.ipynb')).toBeEnabled()
})

test('uploading an existing name asks before replacing it and the old content stays recoverable', async ({ page }) => {
  const original = 'def one() -> int:\n    return 1\n'
  const replacement = 'def two() -> int:\n    return 2\n'
  const read = () => readFileSync(join(process.env.E2E_WORK_DIR, 'uploads', 'life_replace.ipynb'), 'utf8')
  await uploadNotebook(page, 'life_replace.ipynb', original)
  const before = read()

  const choose = (source) => page.setInputFiles('input[type=file]', {
    name: 'life_replace.ipynb', mimeType: 'application/json', buffer: Buffer.from(notebook(source)),
  })
  await choose(replacement)
  await expect(page.getByRole('alertdialog')).toContainText('life_replace.ipynb')
  await page.getByRole('button', { name: 'Cancel' }).click()
  expect(read()).toBe(before) // cancelling changed nothing

  await choose(replacement)
  await page.getByRole('button', { name: 'Replace', exact: true }).click()
  await expect(page.getByTestId('replaced-notice')).toContainText('The previous content was saved as version')
  expect(read()).not.toBe(before)

  await page.goto('/')
  await button(page, 'Versions', 'life_replace.ipynb').click()
  const versions = page.getByTestId('version-life_replace.ipynb')
  await expect(versions).toHaveCount(1)
  await versions.first().getByRole('button', { name: /^Restore the version of life_replace.ipynb/ }).click()
  await page.getByRole('button', { name: 'Confirm restore' }).click()
  await expect(page.getByTestId('note-life_replace.ipynb')).toContainText('Restored the version saved')
  expect(read()).toBe(before)
})

test('tags and a description can be edited, persist across a reload and filter the library', async ({ page, request }) => {
  await importBundle(request, 'life_tags.ipynb', 'DATA')
  await importBundle(request, 'life_untagged.ipynb', 'DATA')
  await page.goto('/')
  await button(page, 'Details', 'life_tags.ipynb').click()
  await page.getByPlaceholder('Add a tag').fill('production')
  await page.getByRole('button', { name: 'Add tag' }).click()
  await page.getByLabel('Description').fill('Scores customers')
  await page.getByRole('button', { name: 'Save details' }).click()
  await expect(page.getByTestId('description-life_tags.ipynb')).toHaveText('Scores customers')

  await page.reload()
  await expect(page.getByTestId('description-life_tags.ipynb')).toHaveText('Scores customers')
  await expect(page.getByLabel('Tags of life_tags.ipynb')).toContainText('production')
  await expect(page.getByText('life_untagged.ipynb', { exact: true })).toBeVisible()
  await page.getByLabel('Filter by tag').selectOption('production')
  await expect(page.getByText('life_untagged.ipynb', { exact: true })).toHaveCount(0)
  await expect(page.getByText('life_tags.ipynb', { exact: true })).toBeVisible()
})

test('compile history lists a compile and an admin can clear it after confirming', async ({ page, request }) => {
  await importBundle(request, 'life_hist.ipynb', 'DATA')
  const compiled = await request.post('/api/compile', { data: { notebook_path: 'life_hist.ipynb' } })
  expect(compiled.ok()).toBeTruthy()
  await page.goto('/')
  await page.getByRole('button', { name: 'Show compile and deploy history' }).click()
  await expect(page.getByTestId('history-compile')).toContainText('life_hist.ipynb')
  await page.getByRole('button', { name: 'Clear compile history' }).click()
  await expect(page.getByTestId('history-compile')).toBeVisible() // nothing cleared before confirming
  await page.getByRole('button', { name: 'Yes, clear compile history' }).click()
  await expect(page.getByText(/No compile history yet/)).toBeVisible()
})
