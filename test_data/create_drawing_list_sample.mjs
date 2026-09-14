import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const workspace = "C:/Users/62301/Desktop/部材リスト";
const outputDir = path.join(workspace, "outputs", "01a05d12-c37e-7963-95ee-47146f559f0a");
const inputPath = path.join(outputDir, "示例部材表_可变断面行数.xlsx");
const outputPath = path.join(outputDir, "示例部材表_含图纸列表.xlsx");
await fs.mkdir(outputDir, { recursive: true });

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
const sheet = workbook.worksheets.add("图纸列表");
sheet.showGridLines = false;
sheet.freezePanes.freezeRows(3);

sheet.getRange("A1:B1").values = [["本次提出日期", new Date("2026-09-02T00:00:00")]];
sheet.getRange("B1").format.numberFormat = "yyyy-mm-dd";
sheet.getRange("A3:J5").values = [
  ["图纸名称", "图纸编号", "0", "1", "2", "3", "4", "5", "6", "7"],
  ["16F 梁部材表", "A-016", new Date("2026-08-01T00:00:00"), new Date("2026-09-02T00:00:00"), null, null, null, null, null, null],
  ["17F 梁部材表", "A-017", new Date("2026-08-01T00:00:00"), new Date("2026-08-20T00:00:00"), null, null, null, null, null, null],
];
sheet.getRange("C4:J5").format.numberFormat = "yyyy-mm-dd";
sheet.getRange("A1:B1").format = {
  fill: "#DCE6F1",
  font: { bold: true, color: "#1F2937" },
  borders: { preset: "all", style: "thin", color: "#AAB7C4" },
};
sheet.getRange("A3:J3").format = {
  fill: "#0F4C5C",
  font: { bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  borders: { preset: "all", style: "thin", color: "#B8C7CC" },
};
sheet.getRange("A4:J5").format = {
  borders: { preset: "all", style: "thin", color: "#D9E2E5" },
  verticalAlignment: "center",
};
sheet.getRange("A4:B5").format.fill = "#EFF6F7";
sheet.getRange("A1:J5").format.rowHeight = 23;
const widths = [24, 16, 14, 14, 14, 14, 14, 14, 14, 14];
for (let column = 0; column < widths.length; column += 1) {
  sheet.getRangeByIndexes(0, column, 5, 1).format.columnWidth = widths[column];
}

const inspection = await workbook.inspect({
  kind: "table",
  range: "'图纸列表'!A1:J5",
  include: "values,formulas",
  tableMaxRows: 8,
  tableMaxCols: 10,
  maxChars: 5000,
});
console.log(inspection.ndjson);

const preview = await workbook.render({
  sheetName: "图纸列表",
  range: "A1:J5",
  scale: 1.5,
  format: "png",
});
await fs.writeFile(
  path.join(outputDir, "示例图纸列表_预览.png"),
  new Uint8Array(await preview.arrayBuffer()),
);

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(outputPath);
