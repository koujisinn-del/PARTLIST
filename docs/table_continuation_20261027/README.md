# 同页自动续表验收样张

全部使用虚构数据，图中标为 QA ONLY，不是工程成果。

- `table_continuation.pdf`：两页 A3 横向，来自同一套实际 CAD 几何。
- `whole_groups.dxf` / `.dwg`：110 行、三个部件组 G1 / g1 / G2，分别 35 / 50 / 25 行。优先整组换列，形成 35 行和 75 行两张表；G1 与 g1 保持大小写区别。
- `long_group.dxf` / `.dwg`：同一部件 G100 共 100 行，按单列容量拆成 84 行和 16 行；续列再次显示 G100，并重复表头。
- `validation.json`：各列行数、部件名、字高、行高及 DWG 回读结果。

两页表格保持字高 1 mm、行高 2.5 mm。续表采用实测列宽，因此页面可能留白，不会放大或缩小字体来填满页面。符号文字与断面保持同字号、同基线。

请在 AutoCAD LT 2024 中实际打开 DWG，核对文字可编辑、表头重复、材料符号显示和部件组跨列情况。本机自动校验不能替代现场打开验收。本目录保留纸面毫米尺度的底层排版样张；当前主程序已支持 [100 / 150 / 200 输出倍率](../CAD输出倍率说明.md)，默认 150，本目录原始 CAD 未重新缩放。

复现：在项目 Python 环境执行 `python -B tools/generate_table_continuation_example.py`；输出在 `output/pdf/table_continuation_20261027/`，不修改真实材质库或原始 Excel。
