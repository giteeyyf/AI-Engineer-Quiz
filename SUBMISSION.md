# SUBMISSION

Keepstack 素材检索与结果导出改造作业的交付说明。

## 1. 仓库与分支

| 项 | 值 |
| --- | --- |
| Fork | https://github.com/giteeyyf/AI-Engineer-Quiz |
| 分支 | `feat/library-filter-export`（https://github.com/giteeyyf/AI-Engineer-Quiz/tree/feat/library-filter-export） |
| 起点 | `main` @ `3aa137ccde6f981981582f6f71b81448c72a9dab`（收题时的上游 HEAD） |
| 提交方式 | 按开发顺序多次提交，未 squash / amend / rebase；最终 commit SHA 见分支 HEAD |

## 2. 环境与运行

需要 Python 3.10+。以下命令在仓库根目录执行。

### 方式 A：uv（与 README 一致）

```bash
uv venv --python 3.12
uv pip install -e '.[dev]'
export KEEPSTACK_AI_ENABLED=false
export KEEPSTACK_OCR_ENABLED=false
uv run --no-sync python -m keepstack seed --demo
uv run --no-sync python -m keepstack
```

### 方式 B：标准 venv（本机实测）

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -e ".[dev]"
KEEPSTACK_AI_ENABLED=false KEEPSTACK_OCR_ENABLED=false .venv/Scripts/python -m keepstack seed --demo
KEEPSTACK_AI_ENABLED=false KEEPSTACK_OCR_ENABLED=false .venv/Scripts/python -m keepstack
```

打开 http://localhost:8000，账号 `admin` / `admin`。

数据说明：

- `seed`：24 个素材、3 个集合，与上游说明一致；已补 `approved` / `campaign` / `internal` 等业务标签，并让 6 条素材共用同一 `created_at`（另有 4 条并列），用于验证同时间戳分页。
- `seed --demo`：追加 28 条 `Harbor Campaign` 素材（含 2 个 txt 文档），共 52 条、4 个集合。业务场景在此集合中有 16 条匹配，每页 12 条正好两页。

重复执行 seed 是安全的：内容寻址去重、标签写入用 `INSERT OR IGNORE`、集合按名称复用；实测连跑两次仍为 52 条素材、4 个集合。

## 3. 复现业务场景

1. 登录后用左侧 Collections 进入 `Harbor Campaign`，或在 Library 的 Collection 下拉里选择它。
2. 搜索框输入 `harbor`。
3. 点 Tags 按钮，在弹出面板里勾选 `approved`、`campaign` 的 Include，勾选 `internal` 的 Exclude，点 Done。
4. 页面顶部显示生效条件 chips，底部显示 `Page 1 / 2 · 16 matching assets`，可上一页 / 下一页。
5. 排序下拉切换 `Created: newest first` / `oldest first`，切换后回到第 1 页。
6. 点 `Export CSV` 下载当前全部匹配（不受分页限制）。

等价的命令行验证：

```bash
TOKEN=$(curl -s -X POST localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"admin"}' | python -c "import sys,json;print(json.load(sys.stdin)['token'])")

curl -s "localhost:8000/api/assets?q=harbor&collection_id=4&tags=approved&tags=campaign&exclude_tags=internal&sort=newest&page=1&page_size=12" \
  -H "Authorization: Bearer $TOKEN"

curl -s -o export.csv "localhost:8000/api/assets/export.csv?q=harbor&collection_id=4&tags=approved&tags=campaign&exclude_tags=internal&sort=newest" \
  -H "Authorization: Bearer $TOKEN"
```

`collection_id` 以实际 `GET /api/collections` 返回为准。

## 4. 实测结果

本机 Windows 10、Python 3.12.15、Edge 无头浏览器实测。

| 项目 | 结果 |
| --- | --- |
| 全量测试 | `36 passed, 2 skipped`，约 3 秒 |
| `seed` | 24 素材 / 3 集合，默认分页 2 页 |
| `seed --demo` | 52 素材 / 4 集合，约 7 秒 |
| 场景筛选 | total 16，pages 2；第 1 页 12 条，第 2 页 4 条，两页 uuid 无交集 |
| 导出 | 16 条数据 + 表头，2900 字节；UTF-8 BOM、CRLF；下载文件名 `素材清单-YYYYMMDD-HHMMSS.csv` |
| 媒体类型导出 | 筛选 `document` 导出 2 行，`media_type` 全部为 document |
| 特殊字符 | `q=!!!` 返回 200 且 total 0；`q=100%`、`q=a-b` 返回 200 |
| 非法分页 | `page=0`、`page_size=999`、`page=abc` 返回 422；`page=99` 返回 200、items 空、total 真实 |
| 未登录导出 | 401 |
| 修改后一致 | 改标题/描述/标签后，下一次列表、筛选与导出立即反映 |
| 矛盾标签（接口） | `tags=approved&exclude_tags=approved` 返回 0 条，导出仅表头 |
| 失败态 | 列表请求中断显示横幅并保留上次结果，重试后恢复；导出中断弹 `Export failed` 提示 |

浏览器自动化走通了完整路径：登录 → 选集合 → 输入关键词与标签 → 翻页 → 切换排序 → 下载 CSV，页面无错误横幅。

## 5. 实现概要

### 5.1 后端

- `keepstack/search.py`
  - `build_filters()` 生成唯一的 WHERE / ORDER 片段，count、分页列表、CSV 三处共用。
  - 关键词用 `a.id IN (SELECT rowid FROM asset_fts WHERE asset_fts MATCH ?)`，沿用现有 FTS 字段与 porter 分词。
  - 非字母数字统一清洗；清洗后无词元时用恒假谓词 `0`，避免 `MATCH ''` 报错，也不放宽成全库。
  - 包含标签每个一条 `EXISTS`（天然 AND），排除标签一条 `NOT EXISTS ... IN (...)`；去重后若包含集与排除集相交，直接返回 `WHERE 0`。
  - 排序统一为 `created_at + id` 同向兜底，关键词存在时也遵守所选排序，不再被 bm25 覆盖。
  - 响应增加 `page`、`page_size`、`pages`、`applied`（含集合名），卡片增加 `description` 与 `tags`。
- `keepstack/app.py`
  - `GET /api/assets` 支持重复 `tags` / `exclude_tags`、`page` / `page_size`（1 到 100），保留 `limit` / `offset` 兼容旧调用。
  - 新增 `GET /api/assets/export.csv`，注册在 `/api/assets/{uuid}` 之前，避免被 uuid 路由吞掉。
  - PATCH 中 `reindex_embedding` 失败不影响已提交的编辑，FTS 保持同步。
- `keepstack/export.py`（新增）
  - 流式输出，每 200 行一块；`csv.writer` 处理逗号、引号、换行。
  - UTF-8 BOM；CRLF；标签用 `|` 连接并按名称排序；零结果只输出表头。
  - 响应头同时给 ASCII 回退名与 `filename*=UTF-8''`。
- `keepstack/db.py`
  - 新增 `assets(created_at, id)`、`asset_tags(tag_id, asset_id)`、`collection_assets(collection_id, asset_id)` 索引。
  - 新增 `close_conn()`，供测试隔离与 Windows 下释放文件句柄。
- `keepstack/seed.py` / `keepstack/__main__.py`
  - 业务标签、固定时间戳（含并列）、中文/引号/换行描述。
  - `seed --demo` 追加 campaign 数据集。

### 5.2 前端

- 筛选栏：集合下拉、标签面板（全量标签 + 搜索框，每行 Include / Exclude 两个复选框，同一标签两侧互斥）、清除按钮。
- 侧栏标签：点标签名切换包含，点 `×` 切换排除；统计数字标注为全库口径，不冒充当前匹配数。
- 生效条件 chips 由后端 `applied` 回显渲染，单个可删，另有清除全部。
- 面板里同一标签只能选 Include 或 Exclude 一侧；接口仍按需求对两侧冲突返回空结果。
- 分页器：上一页 / 第 x / y 页 / 共 n 条 / 下一页。
- 导出：`fetch` 带 Bearer token 取 blob，解析 `Content-Disposition` 中文文件名，失败用 toast 提示。
- 请求竞态：`AbortController` + 递增序号，迟到的旧响应直接丢弃；筛选或排序变化统一回到第 1 页。
- 空结果区分「无条件」与「有筛选」两种文案；加载失败显示横幅与重试按钮，保留上次成功结果。
- 语义搜索入口隐藏，元素保留在 DOM 中，避免顶层监听报错导致整页脚本失效。

## 6. 主要取舍

| 取舍 | 理由 |
| --- | --- |
| 标签筛选用 EXISTS 而非 JOIN + GROUP BY | 每条素材恒为一行，`COUNT(*)`、分页、导出天然同构，不会出现重复行或计数偏差 |
| 排序用 `(created_at, id)` 同向兜底 | id 唯一，构成全序；正序与倒序互为严格逆序，同时间戳跨页不重不漏 |
| `page/page_size` 为主契约 | 与上一页 / 下一页语义直接对应；旧 `limit/offset` 保留兼容 |
| 越界页返回 200 空列表 | 数据变化导致的末页收缩是常态，404 会让前端把正常状态当故障 |
| CSV 用流式响应 | 行数增长时内存有界；本场景 16 行，收益在极端情况下体现 |
| 浏览器用 `fetch + blob` 下载 | `<a href>` 不带 Authorization 头，签名 URL 会新增公开路由与凭据面 |
| 标签用 `|` 分隔 | 不会与 CSV 的逗号冲突，Excel 打开不会误拆列 |
| `seed --demo` 而不是扩充默认 seed | 保持 README 的 24 条口径，同时给评审一条命令拿到多页场景 |
| 前端零构建增量改造 | 保留原技术栈与页面结构，降低评审阅读成本 |

### 6.1 新增组件的收益与成本

| 新增组件 | 收益 | 成本 |
| --- | --- | --- |
| `keepstack/export.py` | CSV 细节集中在一处，复用同一份筛选与排序；流式输出内存有界 | 多一个模块；流式中途异常会截断下载，需要单独说明 |
| `db.py` 三个复合索引 | 排序与标签/集合 EXISTS 过滤走索引，数据量增长后仍然稳定 | 写入略慢，库文件略大 |
| `tests/conftest.py` + `test_library.py` | 14 个接口级用例覆盖验收点，改坏接口会立即变红 | 需要维护固定夹具与逐用例清库逻辑 |
| 前端筛选栏、chips、分页器 | 条件可见、可改、可清；chips 只渲染后端回显，状态不会说谎 | 原生 JS 状态变多，需要 AbortController 与序号防竞态 |
| `seed --demo` | 一条命令复现多页业务场景，不污染默认 24 条口径 | 种子多 28 条素材，生成约 5 秒 |
| `db.close_conn()` | 测试可安全重定向数据目录；Windows 下能及时释放文件句柄 | 多一个公共函数需要维护 |

## 7. 修复的范围外缺陷

- `web/app.js` 的 `api()` 在传 `json` 时未设置请求方法，`fetch` 默认 GET 且带 body，浏览器直接抛错。表现为登录、新建集合等所有 JSON 写操作失效。已改为 `json` 请求默认 POST（显式传入的方法优先）。不修此缺陷无法从页面完成任何操作。
- PATCH 编辑后 `reindex_embedding` 抛错会让已提交的修改返回 500。已用 try/except 隔离，FTS 仍在返回前同步完成。

## 8. 限制与未完成项

限制：

- 语义搜索未纳入新的筛选与排序，入口已隐藏；接口仍保留，供旧调用与相似素材使用。
- 标签为完整名称精确匹配，无模糊与前缀；CJK 没有分词，连续中文按单个 token 前缀匹配。
- 侧栏 facets 与标签计数是全库统计，不随筛选联动，界面已标注 `(catalog)`。
- OFFSET 分页在并发写入下可能漂移；本次验收口径为数据不变，未引入 keyset 分页。
- 标签面板只列出目录里已存在的标签，不支持在筛选时新建标签；新建标签仍在素材详情里完成。
- `status` 查询参数保留但列表恒为 `active`，传其他值不会放宽结果。
- 导出列为固定集合，不支持自选列；导出不含素材文件。
- 导出采用流式响应，读库中途异常会留下截断的 CSV；正常查询不会触发。
- 未做跨请求快照，列表与导出分别查询。

未完成项：

- 筛选条件未同步到 URL，刷新或分享链接不能还原条件。
- 未提供页大小切换（固定 12）。
- 未补语义搜索的自动化测试。
- 未做大数据量下的导出耗时与内存压测。

## 9. Windows 下 pytest 启动报 WinError 5

现象：pytest 还没收集用例就退出，报 `PermissionError: [WinError 5] 拒绝访问`，路径是
`C:\Users\<user>\AppData\Local\Temp\pytest-of-Windows\pytest-current`。

原因：`pytest-current` 是 pytest 每次会话重建的目录符号链接。上一次运行被中断，或两次
pytest 同时运行时，这个重解析点可能损坏；下次启动在 `cleanup_dead_symlinks` 里对它
`os.stat`，Windows 直接拒绝访问。与项目代码无关。

处理（PowerShell）：

```powershell
$root = Join-Path $env:TEMP "pytest-of-Windows"
if (Test-Path (Join-Path $root "pytest-current")) {
    (Get-Item (Join-Path $root "pytest-current") -Force).Delete()
}
```

或者绕开系统临时目录：

```powershell
.\.venv\Scripts\python.exe -m pytest -q --basetemp=.pytest-tmp
```

`.pytest-tmp/` 已加入 `.gitignore`。不要同时开两个 pytest 会话指向同一个临时根目录。

