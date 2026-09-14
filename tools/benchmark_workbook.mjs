import fs from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { FileBlob, SpreadsheetFile } from '@oai/artifact-tool';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const scratch = path.join(root, 'tmp', 'benchmark_20260904');
await fs.mkdir(scratch, { recursive: true });
const input = path.join(root, 'テスト用部材データ0904.xlsx');
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(input));
const mode = process.argv[2] ?? 'inspect';
if (mode === 'inspect') {
  console.log((await workbook.inspect({ kind: 'sheet', include: 'id,name', maxChars: 3000 })).ndjson);
  console.log((await workbook.inspect({ kind: 'table', range: "'図面リスト'!A1:T18", tableMaxRows: 18, tableMaxCols: 20, maxChars: 7000 })).ndjson);
  console.log(workbook.help('worksheet.copy', { include: 'index,examples,notes', maxChars: 5000 }).ndjson);
  for (const [name, range] of [['図面リスト', 'A1:M12'], ['2Fリスト', 'A1:G14'], ['3Fリスト', 'A1:G14']]) {
    const preview = await workbook.render({ sheetName: name, range, scale: 1.5, format: 'png' });
    await fs.writeFile(path.join(scratch, `source_${name}.png`), new Uint8Array(await preview.arrayBuffer()));
  }
}
if (mode === 'create') {
  const listing = workbook.worksheets.getItem('図面リスト');
  const metadata = [listing.getRange('A5:W5').values[0], listing.getRange('A9:W9').values[0]];
  const plans = [];
  for (let floor = 4; floor <= 13; floor++) {
    const sourceName = floor % 2 === 0 ? '2Fリスト' : '3Fリスト';
    const source = workbook.worksheets.getItem(sourceName);
    const rowCount = floor % 2 === 0 ? 165 : 217;
    const target = workbook.worksheets.add(`${floor}Fリスト`);
    const address = `A1:F${rowCount}`;
    target.getRange(address).copyFrom(source.getRange(address), 'all');
    // Range copy does not carry worksheet merge definitions.
    target.getRange('C1:D1').merge();
    for (const col of ['A', 'B', 'C', 'D', 'E', 'F']) {
      const width = source.getRange(`${col}1:${col}${rowCount}`).format.columnWidth;
      if (Number.isFinite(width)) target.getRange(`${col}1:${col}${rowCount}`).format.columnWidth = width;
    }
    for (let row = 1; row <= rowCount; row++) {
      const height = source.getRange(`A${row}:F${row}`).format.rowHeight;
      if (Number.isFinite(height)) target.getRange(`A${row}:F${row}`).format.rowHeight = height;
    }
    if (JSON.stringify(target.getRange(address).values) !== JSON.stringify(source.getRange(address).values)) {
      throw new Error(`Copied values differ: ${floor}F`);
    }
    if (JSON.stringify(target.getRange(address).formulas) !== JSON.stringify(source.getRange(address).formulas)) {
      throw new Error(`Copied formulas differ: ${floor}F`);
    }
    plans.push({ name: `${floor}Fリスト`, copiedFrom: sourceName, dataRows: rowCount - 1 });
  }
  // This is benchmark-only metadata: give every synthetic floor the same
  // revision workload as the floor from which its member rows were copied.
  const records = [];
  for (let floor = 2; floor <= 13; floor++) {
    const record = metadata[floor % 2].map(value =>
      typeof value === 'number' && value > 40000
        ? new Date(Date.UTC(1899, 11, 30) + value * 86400000) : value);
    record[0] = `${floor}Fリスト`;
    if (floor >= 4) record[1] = `TEST-${String(floor).padStart(2, '0')}`;
    records.push(record);
  }
  listing.getRange('A4:W23').clear({ applyTo: 'contents' });
  listing.getRange('A4:W15').values = records;
  listing.getRange('C4:W15').setNumberFormat('yy.mm.dd');
  console.log((await workbook.inspect({ kind: 'sheet', include: 'id,name', maxChars: 4000 })).ndjson);
  console.log((await workbook.inspect({ kind: 'match', searchTerm: '#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A', options: { useRegex: true, maxResults: 10 }, maxChars: 2000 })).ndjson);
  for (const name of ['図面リスト', '2Fリスト', '3Fリスト', ...plans.map(item => item.name)]) {
    const range = name === '図面リスト' ? 'A2:F15' : 'A1:F12';
    const preview = await workbook.render({ sheetName: name, range, scale: 1, format: 'png' });
    await fs.writeFile(path.join(scratch, `expanded_${name}.png`), new Uint8Array(await preview.arrayBuffer()));
  }
  const outdir = path.join(root, 'outputs', '01a05d12-c37e-7963-95ee-47146f559f0a', 'benchmark_20260904');
  await fs.mkdir(outdir, { recursive: true });
  const outputPath = path.join(outdir, '测试用_2F至13F.xlsx');
  await (await SpreadsheetFile.exportXlsx(workbook)).save(outputPath);
  await fs.writeFile(path.join(scratch, 'workbook_plan.json'), JSON.stringify({ input, outputPath, plans }, null, 2));
  console.log(`CREATED ${outputPath}`);
}
