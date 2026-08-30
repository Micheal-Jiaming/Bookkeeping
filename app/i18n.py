"""Interface language: English, or Chinese.

Two decisions shape this file.

**The English text is the key.** There is no catalogue of symbolic names, no
`.po` files and no gettext dependency: ``t("Save draft")`` looks the English up
and returns the Chinese. The cost is that editing an English string silently
orphans its translation; the benefit is that the source reads as prose rather
than as ``t("btn.save_draft")``, and a missing entry falls back to showing the
English rather than a key. ``tests/test_i18n.py`` guards the orphan case by
checking every entry here still appears somewhere in ``app/ui``.

**Chinese only.** The user asked for exactly one additional language and said so
explicitly. The structure would take more, and that is not an invitation: every
language added has to be maintained against 200-odd strings for ever. Do not add
one speculatively.

Not everything is translated. Format templates (``"Engine %-9s %s"``), ttk style
names and file-dialog patterns pass through untouched, because they are not
things a person reads. Fragments that are concatenated with numbers are
translated only where Chinese word order survives it.
"""

from __future__ import annotations

# Code -> the name shown in the menu, in its own language.
LANGUAGES: dict[str, str] = {"en": "English", "zh": "中文"}
DEFAULT_LANGUAGE = "en"

# Segoe UI has no Chinese glyphs. Windows will substitute something, but the
# result is a mix of two typefaces at different weights on the same line; naming
# a CJK face outright is tidier and Microsoft YaHei UI ships with Windows.
FONT_FOR: dict[str, str] = {"en": "Segoe UI", "zh": "Microsoft YaHei UI"}

_current = DEFAULT_LANGUAGE


def set_language(code: str) -> None:
    """Switch language. An unknown code falls back to English rather than raising."""
    global _current
    _current = code if code in LANGUAGES else DEFAULT_LANGUAGE


def current() -> str:
    return _current


def font_family() -> str:
    return FONT_FOR.get(_current, FONT_FOR[DEFAULT_LANGUAGE])


def t(text: str) -> str:
    """The interface text for ``text`` in the current language."""
    if _current == "en":
        return text
    return CHINESE.get(text, text)


CHINESE: dict[str, str] = {
    # --- menus and navigation ---------------------------------------------
    "File": "文件",
    "View": "视图",
    "Help": "帮助",
    "Theme": "主题",
    "Language": "语言",
    "Receipts": "收据",
    "Reports": "报表",
    "Categories & rules": "分类与规则",
    "Settings": "设置",
    "Add receipt images…   Ctrl+O": "添加收据图片…   Ctrl+O",
    "Add receipt by hand   Ctrl+N": "手动添加收据   Ctrl+N",
    "Paste image from clipboard   Ctrl+V": "从剪贴板粘贴图片   Ctrl+V",
    "Export line items to CSV…": "导出明细为 CSV…",
    "Open data folder": "打开数据文件夹",
    "Open log file": "打开日志文件",
    "Refresh   F5": "刷新   F5",
    "Exit   Ctrl+Q": "退出   Ctrl+Q",
    "About ": "关于 ",
    "Dark": "深色",
    "Light": "浅色",
    "Dracula": "德古拉",
    "Solarized": "日晒",

    # --- category names, which live in the database ------------------------
    # Translated where they are displayed and matched back to the English when
    # a category is chosen, exactly as the engine picker is (11.38). What is
    # stored is always the English name, so the books do not change language.
    "Groceries": "杂货",
    "Dining": "餐饮",
    "Household": "家居用品",
    "Personal Care": "个人护理",
    "Health & Pharmacy": "健康与药品",
    "Clothing": "服装",
    "Electronics": "电子产品",
    "Baby & Kids": "母婴儿童",
    "Pets": "宠物",
    "Transport & Fuel": "交通与燃油",
    "Entertainment": "娱乐",
    "Office & Supplies": "办公用品",
    "Fees & Taxes": "费用与税金",
    "Other": "其他",
    "Uncategorized": "未分类",

    # --- the receipts page -------------------------------------------------
    "＋ Add receipt images": "＋ 添加收据图片",
    "＋ Add line": "＋ 添加一行",
    "Paste image": "粘贴图片",
    "Add by hand": "手动添加",
    "Show": "显示",
    "Search": "搜索",
    "All": "全部",
    "Needs review": "待复核",
    "Confirmed": "已确认",
    "Scanning": "识别中",
    "Failed": "失败",
    "Date": "日期",
    "Merchant": "商家",
    "Category": "类别",
    "Items": "项数",
    "Total": "合计",
    "Status": "状态",
    "Item": "项目",
    "Qty": "数量",
    "Amount": "金额",
    "Subtotal": "小计",
    "Tax": "税额",
    "Tip": "小费",
    "Currency": "货币",
    "Payment": "支付方式",
    "Notes": "备注",
    "Line items": "明细",
    "Date (YYYY-MM-DD)": "日期 (YYYY-MM-DD)",
    "Category for the whole receipt": "整张收据的类别",
    "Save & confirm": "保存并确认",
    "Save draft": "保存草稿",
    "Re-scan": "重新识别",
    "Output": "引擎输出",
    "Delete": "删除",
    "No receipt selected": "未选择收据",
    "Choose a receipt on the left, or add one.": "请在左侧选择一张收据，或添加一张。",
    "No receipts yet.\n\nUse “Add receipt images” to scan one,\nor “Add by hand” to type one in.":
        "还没有收据。\n\n用“添加收据图片”识别一张，\n或用“手动添加”自行录入。",
    "No image\n(entered by hand)": "无图片\n（手动录入）",
    "Image could not be shown": "无法显示图片",
    "click to open full size": "点击查看原图",
    "Engine output — receipt #": "引擎输出 — 收据 #",
    "Receipt #": "收据 #",
    "Delete receipt #": "删除收据 #",
    " and its image?\n\nThis cannot be undone.": " 及其图片？\n\n此操作无法撤销。",
    " deleted": " 已删除",
    "Re-scanning receipt #": "正在重新识别收据 #",
    "That receipt is already being read.": "该收据正在识别中。",
    "A confirmed receipt needs at least a date and a total.\n\nFill those in (they are on the right of the image) and try again.":
        "确认收据至少需要日期和合计金额。\n\n请在图片右侧填写后重试。",
    "Could not open the image:\n": "无法打开图片：\n",
    "Lines: ": "明细合计：",
    "   (off by ": "   （相差 ",
    "confidence ": "置信度 ",
    " tokens": " 个 token",
    "from barcode: ": "条码查得：",
    "needs review": "待复核",
    "not scanned": "未识别",
    "saved as a draft": "已保存为草稿",
    "scanning…": "识别中…",
    "queued": "排队中",
    "confirmed": "已确认",
    "failed": "失败",

    # --- the reports page --------------------------------------------------
    "Total spend": "总支出",
    "Average receipt": "单张平均",
    "Tax paid": "已付税额",
    "Awaiting review": "待复核",
    "Spend by category": "按类别支出",
    "Spend by month": "按月份支出",
    "Top merchants": "主要商家",
    "Apply": "应用",
    "Export CSV…": "导出 CSV…",
    "From / to (YYYY-MM-DD)": "起止日期 (YYYY-MM-DD)",
    "30 days": "30 天",
    "90 days": "90 天",
    "1 year": "1 年",
    "All time": "全部时间",
    "Confirmed only": "仅已确认",
    "Confirmed + unreviewed": "已确认 + 未复核",
    "No months to show yet.": "暂无可显示的月份。",
    "Nothing confirmed in this range yet.": "此区间内还没有已确认的收据。",
    "in this period": "本期间",
    "not counted here": "未计入",
    "nothing pending": "无待处理",
    " line items": " 条明细",
    " receipt(s)": " 张收据",
    " actually spent.": " 的实际支出。",
    "Category figures come from line items, which exclude tax; the difference between a receipt's total and its lines is shown as “Tax & unitemised”, so the categories add up to the $":
        "类别金额来自明细，不含税；收据合计与明细之差计入“税与未分项”，因此各类别相加等于 $",

    # --- categories and rules ----------------------------------------------
    "Categories": "类别",
    "Keyword rules": "关键词规则",
    "New category": "新建类别",
    "New rule": "新建规则",
    "Add": "添加",
    "Add rule": "添加规则",
    "Delete selected": "删除所选",
    "Name": "名称",
    "Lines": "行数",
    "Pattern": "模式",
    "Matches": "匹配",
    "Pri": "优先级",
    "Item name contains": "项目名称包含",
    "Merchant contains": "商家名称包含",
    "plain text": "纯文本",
    "regular expression": "正则表达式",
    "  (regex)": "  （正则）",
    "Re-apply rules to unconfirmed receipts": "对未确认的收据重新应用规则",
    " lines recategorised.": " 条明细已重新分类。",
    " of ": " / ",
    "Added. Use “Re-apply rules” to backfill existing receipts.":
        "已添加。使用“重新应用规则”可回填已有收据。",
    "Choose a category for the rule.": "请为该规则选择一个类别。",
    "Priority must be a whole number.": "优先级必须是整数。",
    "Select a category to delete first.": "请先选择要删除的类别。",
    "Select a rule to delete first.": "请先选择要删除的规则。",
    "Delete the category “": "删除类别“",
    "”?\n\nAny line items using it move to Uncategorized.":
        "”？\n\n使用该类别的明细将移至“未分类”。",
    "The buckets the reports are built from. These names are also the choices offered to the recognition model.":
        "报表即以这些类别为基础。这些名称同时也是提供给识别模型的选项。",
    "Order of precedence: what you set by hand wins, then item-name rules, then the model's own suggestion, then merchant rules, then Uncategorized. Merchant rules sit below the model on purpose — “everything from this shop is Groceries” is a safety net, not a better answer than a look at the item. Lower priority numbers run first.":
        "优先级顺序：手动设置最优先，其次是项目名称规则，然后是模型自身的建议，再是商家规则，最后是“未分类”。商家规则刻意排在模型之后 — “这家店买的都算杂货”只是兜底，并不比看一眼项目更准确。优先级数字越小越先执行。",

    # --- settings ----------------------------------------------------------
    "Recognition": "识别",
    "Engine": "引擎",
    "Model": "模型",
    "Effort": "思考强度",
    "Anthropic API key": "Anthropic API 密钥",
    "Base URL (optional, for a proxy)": "Base URL（可选，用于代理）",
    "Offline OCR language": "离线 OCR 语言",
    "Tesseract executable (optional)": "Tesseract 可执行文件（可选）",
    "Browse…": "浏览…",
    "Find tesseract.exe": "查找 tesseract.exe",
    "Programs": "程序",
    "All files": "所有文件",
    "Save settings": "保存设置",
    "Saved.": "已保存。",
    "Clear stored key": "清除已保存的密钥",
    "Engine status": "引擎状态",
    "What a scan costs": "识别费用",
    "This computer": "本机",
    "Automatic (prefer English)": "自动（优先英语）",
    "Auto — Claude vision, then offline OCR": "自动 — 先用 Claude 视觉，再用离线 OCR",
    "Claude vision only": "仅 Claude 视觉",
    "Offline OCR only (built into Windows)": "仅离线 OCR（Windows 内置）",
    "Offline OCR only (Tesseract)": "仅离线 OCR（Tesseract）",
    "Manual entry only (no scanning)": "仅手动录入（不识别）",
    "Auto-confirm receipts that pass every arithmetic check": "算术校验全部通过时自动确认收据",
    "Off by default: a reading whose numbers add up can still have the wrong merchant or category.":
        "默认关闭：数字对得上，商家或类别仍可能是错的。",
    "Look product names up online": "联网查询商品名称",
    "Turns 'CLX PLNGR' into 'Clorox Plunger & Toilet Brush'. Sends only the barcode printed beside an item — never the shop, the date or the price — to Open Food Facts and UPCitemdb. Answers are cached, so a name is fetched once and then works offline.":
        "把 'CLX PLNGR' 变成 'Clorox Plunger & Toilet Brush'。只把项目旁印的条码发送给 Open Food Facts 和 UPCitemdb — 绝不发送商家、日期或价格。结果会缓存，因此每个名称只查一次，之后离线可用。",
    "Translate item names into Chinese": "将项目名称翻译成中文",
    "Only used while the interface is in Chinese. Item names are translated as a receipt is scanned and kept, so the review pane never waits on the network.":
        "仅在界面为中文时使用。项目名称在识别收据时翻译并保存，因此复核面板不会等待网络。",
    "Stored key: ": "已保存的密钥：",
    ". Leave this box empty to keep it.": "。留空则保持不变。",
    "No key stored yet. Paste one from console.anthropic.com; it is kept in this copy's own data folder and never sent anywhere except to Anthropic.":
        "尚未保存密钥。可从 console.anthropic.com 复制一个粘贴到此处；它只保存在本程序的数据文件夹中，除发送给 Anthropic 外不会发往任何地方。",
    "Remove the stored API key?\n\nScanning with Claude will stop working until a key is entered again.":
        "要删除已保存的 API 密钥吗？\n\n在重新输入密钥之前，将无法使用 Claude 识别。",
    "Per receipt, at list prices — a short receipt (a few items) and a long one (a 24-line shop, measured):":
        "按目录价计算的每张收据费用 — 一张短收据（几项）与一张长收据（实测 24 行）：",
    "\nEvery scan records what it actually cost; it is shown beside the receipt in the review pane.":
        "\n每次识别都会记录实际费用，并显示在复核面板的收据旁。",
    "\n\nData folder:\n": "\n\n数据文件夹：\n",
    "\n\nThe books, the receipt images and the key all live in that folder. Copy it (with the program) to move everything to another computer.":
        "\n\n账目、收据图片和密钥都保存在该文件夹中。将它与程序一起复制，即可整体迁移到另一台电脑。",

    # --- the window: dialogs and the status bar -----------------------------
    "Bookkeeping": "记账",
    "Images": "图片",
    "Choose receipt images": "选择收据图片",
    "Export line items": "导出明细",
    "The clipboard has no image in it.\n\nCopy a receipt photo or take a screenshot (Win+Shift+S) first.":
        "剪贴板中没有图片。\n\n请先复制一张收据照片，或截图（Win+Shift+S）。",
    "Could not read the clipboard: ": "无法读取剪贴板：",
    "Could not write the file: ": "无法写入文件：",
    "Could not open ": "无法打开 ",
    "There is no log file yet.": "还没有日志文件。",
    "A receipt is still being read. Close anyway?": "仍有收据正在识别。仍要关闭吗？",
    "Wrote ": "已写入 ",
    " line item(s) from confirmed receipts to\n": " 条来自已确认收据的明细，保存至\n",
    " image(s) added.\n\nNot added:\n• ": " 张图片已添加。\n\n未添加：\n• ",
    " receipt(s)…": " 张收据…",
    "Scanning ": "正在识别 ",
    " receipt": " 张收据",
    " to review": " 待复核",
    " confirmed": " 已确认",
    " failed": " 失败",
    " scanning": " 识别中",
    "engine…": "引擎…",
    "no engine — see Settings": "无可用引擎 — 见设置",
    "\n\nReads receipt photos and keeps the expenses in order.\n\nData folder:\n":
        "\n\n读取收据照片，帮你把开支理清楚。\n\n数据文件夹：\n",
    "\n\nRecognition: ": "\n\n识别：",
    "\n\nEverything stays on this computer, apart from the receipt image sent to the Anthropic API when the Claude engine runs.":
        "\n\n除使用 Claude 引擎时会将收据图片发送至 Anthropic API 外，所有数据都保存在本机。",
}
