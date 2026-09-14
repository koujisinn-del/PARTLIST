import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workspace = "C:/Users/62301/Desktop/部材リスト";
const outputDir = path.join(workspace, "outputs", "01a05d12-c37e-7963-95ee-47146f559f0a");
const inputPath = path.join(outputDir, "示例部材表_含图纸列表.xlsx");
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));

const values = await workbook.inspect({
  kind: "table",
  range: "'图纸列表'!A1:J5",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 10,
  maxChars: 5000,
});
console.log(values.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "drawing list formula error scan",
});
console.log(errors.ndjson);

const preview = await workbook.render({
  sheetName: "图纸列表",
  range: "A1:J5",
  scale: 1.5,
  format: "png",
});
await fs.writeFile(
  path.join(outputDir, "示例图纸列表_标红后预览.png"),
  new Uint8Array(await preview.arrayBuffer()),
);
