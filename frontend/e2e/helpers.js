export function notebook(source = 'def add(a: int, b: int) -> int:\n    return a + b\n') {
  return JSON.stringify({
    cells: [{ cell_type: 'code', execution_count: null, metadata: {}, outputs: [], source: source.split(/(?<=\n)/) }],
    metadata: {}, nbformat: 4, nbformat_minor: 5,
  })
}

export async function uploadNotebook(page, name, source) {
  await page.goto('/')
  await page.setInputFiles('input[type=file]', { name, mimeType: 'application/json', buffer: Buffer.from(notebook(source)) })
  await page.getByText(/Found \d+ functions/).waitFor()
}
