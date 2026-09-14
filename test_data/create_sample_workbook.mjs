import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const workspace = "C:/Users/62301/Desktop/部材リスト";
const outputDir = path.join(workspace, "outputs", "01a05d12-c37e-7963-95ee-47146f559f0a");
await fs.mkdir(outputDir, { recursive: true });

const workbook = Workbook.create();

const headers = ["部件名称", "种类", "部位", "断面尺寸", "材质", "接合符号", "备注"];

const sheet16 = workbook.worksheets.add("16F 梁部材表");
const rows16 = [
  headers,
  ["G1", "大梁", "全断面", "H-700×300×13×24", "SN490B", "A", "1个断面"],
  ["G2", "大梁", "端部", "H-800×300×14×26", "SN490B", "B", "2个断面"],
  ["G2", "大梁", "中央", "H-700×300×13×24", "SN490B", "B", ""],
  ["G3", "大梁", "左", "H-900×350×16×28", "SN490B", "C", "3个断面"],
  ["G3", "大梁", "中", "H-800×350×14×26", "SN490B", "C", ""],
  ["G3", "大梁", "右", "H-900×350×16×28", "SN490B", "C", ""],
  ["G4", "大梁", "区间1", "H-950×350×16×32", "SN490B", "D", "4个断面"],
  ["G4", "大梁", "区间2", "H-900×350×16×28", "SN490B", "D", ""],
  ["G4", "大梁", "区间3", "H-850×350×14×28", "SN490B", "D", ""],
  ["G4", "大梁", "区间4", "H-950×350×16×32", "SN490B", "D", ""],
  ["B1", "小梁", "区间1", "H-500×200×10×16", "SN400B", "A", "5个断面"],
  ["B1", "小梁", "区间2", "H-550×200×10×16", "SN400B", "A", ""],
  ["B1", "小梁", "区间3", "H-600×200×11×17", "SN400B", "A", ""],
  ["B1", "小梁", "区间4", "H-550×200×10×16", "SN400B", "A", ""],
  ["B1", "小梁", "区间5", "H-500×200×10×16", "SN400B", "A", ""],
  ["B2", "小梁", "端部", "H-450×200×9×14", "SN400B", "B", "2个断面"],
  ["B2", "小梁", "中央", "H-400×200×8×13", "SN400B", "B", ""],
];
sheet16.getRange(`A1:G${rows16.length}`).values = rows16;

const sheet17 = workbook.worksheets.add("17F 梁部材表");
const rows17 = [
  headers,
  ["G5", "大梁", "区间1", "H-1000×400×18×32", "SN490B", "E", "5个断面"],
  ["G5", "大梁", "区间2", "H-950×400×18×30", "SN490B", "E", ""],
  ["G5", "大梁", "区间3", "H-900×400×16×28", "SN490B", "E", ""],
  ["G5", "大梁", "区间4", "H-950×400×18×30", "SN490B", "E", ""],
  ["G5", "大梁", "区间5", "H-1000×400×18×32", "SN490B", "E", ""],
  ["B3", "小梁", "全断面", "H-350×175×7×11", "SN400B", "A", "1个断面"],
  ["B4", "小梁", "左", "H-450×200×9×14", "SN400B", "C", "3个断面"],
  ["B4", "小梁", "中", "H-400×200×8×13", "SN400B", "C", ""],
  ["B4", "小梁", "右", "H-450×200×9×14", "SN400B", "C", ""],
];
sheet17.getRange(`A1:G${rows17.length}`).values = rows17;

for (const sheet of [sheet16, sheet17]) {
  const used = sheet.getUsedRange();
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  sheet.getRange("A1:G1").format = {
    fill: "#0F4C5C",
    font: { bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: "#B8C7CC" },
  };
  sheet.getRange(`A2:G${used.rowCount}`).format = {
    font: { color: "#1F2937" },
    verticalAlignment: "center",
    borders: { preset: "all", style: "thin", color: "#D9E2E5" },
  };
  sheet.getRange(`A2:B${used.rowCount}`).format.fill = "#EFF6F7";
  sheet.getRange(`A1:G${used.rowCount}`).format.rowHeight = 22;
  const widths = [14, 11, 13, 25, 14, 14, 18];
  for (let index = 0; index < widths.length; index += 1) {
    sheet.getRangeByIndexes(0, index, used.rowCount, 1).format.columnWidth = widths[index];
  }
}

const inspection = await workbook.inspect({
  kind: "sheet,table",
  maxChars: 4000,
  tableMaxRows: 8,
  tableMaxCols: 7,
});
console.log(inspection.ndjson);

for (const sheetName of ["16F 梁部材表", "17F 梁部材表"]) {
  const preview = await workbook.render({
    sheetName,
    autoCrop: "all",
    scale: 1.5,
    format: "png",
  });
  const previewName = sheetName.startsWith("16") ? "sample_16F.png" : "sample_17F.png";
  await fs.writeFile(path.join(outputDir, previewName), new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(path.join(outputDir, "示例部材表_可变断面行数.xlsx"));
console.log(path.join(outputDir, "示例部材表_可变断面行数.xlsx"));
