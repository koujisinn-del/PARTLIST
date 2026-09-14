import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const inputPath = "C:/Users/62301/Desktop/部材リスト/outputs/01a05d12-c37e-7963-95ee-47146f559f0a/示例部材表_可变断面行数.xlsx";
const blob = await FileBlob.load(inputPath);
const workbook = await SpreadsheetFile.importXlsx(blob);

const range16 = await workbook.inspect({
  kind: "table",
  range: "'16F 梁部材表'!A1:G18",
  include: "values,formulas",
  tableMaxRows: 18,
  tableMaxCols: 7,
  maxChars: 8000,
});
console.log(range16.ndjson);

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "final formula error scan",
});
console.log(errors.ndjson);
