"""Produce source application packages with separate, disclosed CAD dependencies."""
from pathlib import Path
from datetime import date
import argparse
import hashlib
import json
import shutil
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]

def build(language, stamp):
    if language not in {"zh", "ja"} or len(stamp) != 8 or not stamp.isascii() or not stamp.isdigit():
        raise ValueError("Use zh/ja and a YYYYMMDD date.")
    name = f"部材表生成器_中文源码_{stamp}" if language == "zh" else f"部材表生成器_日本語ソース_{stamp}"
    destination = ROOT / "release" / (name + ".zip")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Previous package will not be overwritten: {destination}")
    staging_root = ROOT / "tmp" / "language_release"
    staging_root.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=language + "_", dir=staging_root)) / name
    stage.mkdir()
    for filename in ("main.py", "setup_source.py", "requirements.txt", "安装开发环境.cmd", "启动部材表生成器.cmd", "README.md", "项目开发记录.md"):
        shutil.copy2(ROOT / filename, stage / filename)
    shutil.copy2(ROOT / f"launcher_{language}.pyw", stage / "app_launcher.pyw")
    if language == "ja":
        shutil.copy2(ROOT / "启动部材表生成器.cmd", stage / "起動.cmd")
        shutil.copy2(ROOT / "安装开发环境.cmd", stage / "環境セットアップ.cmd")
    for folder, extensions in (("material_drawing_generator", {".py"}), ("site_templates", {".json"}), ("material_libraries", {".json", ".md"})):
        for source in sorted((ROOT / folder).iterdir()):
            if not source.is_file() or source.suffix not in extensions:
                continue
            target = stage / folder / source.name
            target.parent.mkdir(exist_ok=True)
            shutil.copy2(source, target)
    (stage / "tools").mkdir()
    shutil.copy2(ROOT / "tools" / "preview_material_symbols.py", stage / "tools" / "preview_material_symbols.py")
    (stage / "distribution.json").write_text(json.dumps({"language": language}), encoding="utf-8")
    converter = "libredwg-0.14.8594-win64"
    # Preserve upstream runtime, documentation and license files together.
    shutil.copytree(ROOT / converter, stage / converter)
    note = (
        f"中文版源码运行包（{stamp}）\n请先完整解压，不要在 ZIP 中直接启动。\n"
        "使用已有的 Python 3.12 64 位（含 tkinter），先运行安装开发环境.cmd，再运行启动部材表生成器.cmd。\n"
        "安装脚本仅在解压目录建立 .venv 并安装 requirements.txt 依赖，不安装 Python、不要求管理员权限；安装依赖需要可用软件源。\n"
        "已配好环境时，可使用该 Python 直接运行 main.py --lang zh。更新请解压到新目录，不要覆盖旧包。\n"
        "生成器未打成 EXE。libredwg 文件夹是 DWG 读写所需的第三方原生依赖，不是生成器程序；使用须符合公司要求。\n"
        "不附测试 Excel、图框或历史输出，请选择自己的文件。\n"
        "材质功能已整合到主界面：勾选“按材质库生成符号”，点击“材质符号库”查看实际 CAD 样式；无记号显示“无”。\n"
        "Excel 材质列写完整材质名称，断面列只写断面尺寸。基础及自定义规则位于 material_libraries；未填写材质的旧 Excel 可关闭此功能。\n"
        "断面按 Excel 原文输出，不校验类型名单，也不替换或删除记号。字体缺字、换行或排版放不下仍会提示；空白材质仍需补充。\n"
        "基础符号已实现；混合材质 BH 尚未支持，“部位”列在 LT 2024 中的居中仍待现场验证。正式提交前请检查输出。\n"
        "首选配置与现场模板沿用当前 Windows 用户的 LOCALAPPDATA/MaterialDrawingGenerator，不包含开发机个人设置。\n"
        if language == "zh" else
        f"日本語ソース実行パッケージ（{stamp}）\nZIP をすべて展開してから起動してください。\n"
        "既存の Python 3.12 64 ビット（tkinter を含む）を使用し、環境セットアップ.cmd、起動.cmd の順に実行してください。\n"
        "セットアップは展開先の .venv に requirements.txt の依存ライブラリを導入します。Python 本体のインストールや管理者権限は不要です。利用可能なパッケージ取得先が必要です。\n"
        "環境が準備済みの場合は、その Python で main.py --lang ja を実行できます。更新時は旧パッケージを上書きせず、新しいフォルダーに展開してください。\n"
        "生成ツール自体は EXE 化していません。libredwg は DWG 入出力に必要な第三者製ネイティブ依存プログラムです。社内規則に従って使用してください。\n"
        "テスト用 Excel、図枠、過去の出力は同梱していません。ご自身のファイルを選択してください。\n"
        "材質機能はメイン画面に統合済みです。「材質記号を適用」を選び、「材質記号一覧」で CAD と同じ記号を確認できます。無記号は「無」と表示されます。\n"
        "断面は Excel の原文どおりに出力し、種類の制限や記号の置換・削除はしません。フォントの字形不足、改行、領域超過は引き続き通知します。空欄の材質は入力が必要です。\n"
        "Excel の材質列には正式名称、断面列には断面寸法だけを入力してください。基本・カスタム設定は material_libraries にあります。旧 Excel では材質機能をオフにできます。\n"
        "基本記号は実装済みですが、異材質を組み合わせた BH は未対応です。「部位」列の中央表示は LT 2024 実機確認待ちです。正式提出前に出力を確認してください。\n"
        "設定と現場テンプレートは現在の Windows ユーザーの LOCALAPPDATA/MaterialDrawingGenerator を引き続き使用します。開発機の個人設定は同梱しません。\n"
        "画面とこの案内は日本語です。詳細資料、診断ログ、外部依存プログラムのメッセージには原文が含まれます。\n"
    )
    (stage / "README.txt").write_text(note, encoding="utf-8")
    manifest = {path.relative_to(stage).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(stage.rglob("*")) if path.is_file()}
    (stage / "file_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED) as archive:
        for source in sorted(stage.rglob("*")):
            if source.is_file():
                archive.write(source, name + "/" + source.relative_to(stage).as_posix())
    with zipfile.ZipFile(destination) as archive:
        assert archive.testzip() is None
        assert json.loads(archive.read(name + "/distribution.json"))["language"] == language
        for filename, digest in manifest.items():
            assert hashlib.sha256(archive.read(name + "/" + filename)).hexdigest() == digest, filename
        for filename in ("base.json", "custom.json"):
            assert name + "/material_libraries/" + filename in archive.namelist()
    print(destination)
    return destination

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=date.today().strftime("%Y%m%d"))
    args = parser.parse_args()
    for language in ("zh", "ja"):
        build(language, args.date)
