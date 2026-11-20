import { execFileSync } from 'node:child_process'
import { existsSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { expect, test } from '@playwright/test'
import { importArchive, notebook, zip } from './helpers.js'

test.describe.configure({ mode: 'serial' })

const READER = (
  "import json\n"
  + "def weights() -> dict:\n    return json.load(open('weights.json'))\n"
  + "def vocab() -> str:\n    return open('models/vocab.txt').read()\n"
)
const nb = (name, source = READER) => ({ name, data: notebook(source) })

// Calls an endpoint of the app the dashboard generated, in a fresh interpreter, the way a deployed
// copy would run it. Returns the parsed JSON answer.
function callGeneratedApp(appName, path) {
  const apps = join(process.env.E2E_WORK_DIR, 'apps')
  const dir = readdirSync(apps).find((d) => d.startsWith(appName))
  const code = [
    'import importlib, json, sys',
    'from fastapi.testclient import TestClient',
    'sys.path.insert(0, sys.argv[1])',
    'module = importlib.import_module(sys.argv[2] + ".app")',
    'print(json.dumps(TestClient(module.app).post(sys.argv[3], json={}, headers={"X-API-Key": "k"}).json()))',
  ].join('\n')
  const out = execFileSync(process.env.E2E_PYTHON || 'python', ['-c', code, apps, dir, path], {
    env: { ...process.env, NOTEBOOK_API_KEY: 'k' },
  })
  return JSON.parse(out.toString().trim().split('\n').pop())
}

test('an archive with data files imports, compiles, and the app reads them', async ({ page }) => {
  await importArchive(page, 'bundle.zip', zip([
    nb('reader.ipynb'),
    { name: 'weights.json', data: '{"w": 7}' },
    { name: 'models/vocab.txt', data: 'hello world' },
  ]))
  const summary = page.getByTestId('import-summary')
  await expect(summary).toContainText('reader.ipynb')
  await expect(summary).toContainText('Data files kept: weights.json, models/vocab.txt')

  await page.getByText(/Compile Now/).click()
  await expect(page.getByText('Compilation successful')).toBeVisible()
  await expect(page.getByTestId('diagnostics')).toHaveCount(0) // no "file does not exist" warning

  expect(callGeneratedApp('reader', '/weights')).toEqual({ result: { w: 7 } })
  expect(callGeneratedApp('reader', '/vocab')).toEqual({ result: 'hello world' })
})

test('the same notebook without its data file explains what is missing', async ({ page }) => {
  await importArchive(page, 'alone.zip', zip([nb('alone.ipynb')]))
  await expect(page.getByTestId('import-summary')).toContainText('alone.ipynb')
  await page.getByText(/Compile Now/).click()
  await expect(page.getByText('Compilation successful')).toBeVisible()
  const warnings = page.getByTestId('diagnostics')
  await expect(warnings).toContainText('weights.json')
  await expect(warnings).toContainText(/exist/)
})

test('unsafe archive entries are refused and nothing escapes the notebook folder', async ({ page }) => {
  await importArchive(page, 'unsafe.zip', zip([
    nb('safe.ipynb'),
    { name: 'ok.json', data: '{"ok": true}' },
    { name: '../escape.txt', data: 'nope' },
    { name: '/abs/escape.txt', data: 'nope' },
    { name: 'link.json', symlink: '/etc/passwd' },
  ]))
  const summary = page.getByTestId('import-summary')
  await expect(summary).toContainText('Data files kept: ok.json')
  await expect(summary).toContainText('Skipped')
  await expect(summary).toContainText('link.json')
  await expect(summary).toContainText('escape.txt')
  expect(existsSync(join(process.env.E2E_WORK_DIR, 'escape.txt'))).toBe(false)
  expect(existsSync(join(process.env.E2E_WORK_DIR, 'uploads', 'escape.txt'))).toBe(false)
})

test('an archive that is not a zip is rejected with a clear error', async ({ page }) => {
  await importArchive(page, 'broken.zip', Buffer.from('this is not a zip archive'))
  await expect(page.locator('#notebook-upload-error')).toContainText('not a valid zip')
  await expect(page.getByTestId('import-summary')).toHaveCount(0)
})
