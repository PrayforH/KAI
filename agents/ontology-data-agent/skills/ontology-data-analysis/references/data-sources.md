# 数据源执行

先检查实际工具与连接，再声明可用能力。工具存在不代表网络可达或具备授权。

## CSV / XLSX / SQLite

使用本 Skill `scripts/sqlite_readonly.py`，先通过 `--help` 查看参数。脚本只依赖 Python 标准库，在 KAI 不依赖预装 sqlite3 CLI。这是经过版本化的只读执行器，与小树临时编写包装脚本不同；每次调用明确给出库、SQL 文件、超时和结果路径。

```sh
python3 <本Skill目录>/scripts/sqlite_readonly.py --database inputs/example.sqlite --sql-file ontology/example/queries/q01.sql --output ontology/example/results/q01.json --timeout 60 --max-rows 200
```

先在当前工作区的 `.claude/skills/ontology-data-analysis/` 查找本 Skill 的实际目录，必要时在工作区内 Glob 定位；SDK 提供的宿主机临时路径可能不在沙箱中，不要扫描整个文件系统。不能假定 `/mnt` 或小树路径。执行器支持 Python 3.10 及以上，以 SQLite URI mode=ro、query_only、authorizer、禁扩展和进度超时执行单条语句；只允许查询、只读 schema PRAGMA 和 EXPLAIN。结果包含列、行、返回行数与 truncated。执行器失败时查看错误并报告，不自行重写或放宽只读校验。需要完整结果时先控制查询规模，再提高 max-rows（上限 10000）或分页，不称截断结果为全量。

先查询 sqlite_schema，再逐表读取 `pragma_table_info('表名')`、`pragma_foreign_key_list('表名')` 与三行样本。标识符使用双引号，值使用正确转义；不要拼接未经检查的文件名为 SQL。

CSV 通过 csv 标准库、XLSX 通过已安装的 openpyxl/pandas 读取全部相关 sheet，在工作区新建 SQLite 分析副本；不要改输入原件。保留表/列/工作表映射、行数、编码、类型转换与源文件指纹。号码和带前导零的代码保持文本，金额明确数值转换；公式工作表注明缓存是否存在，不把空缓存当零。输入缺依赖时先报告缺件，不偷偷遗漏工作表。

## PostgreSQL

仅当沙箱具有 psql、已配置 PGSERVICE/PGPASSFILE 等凭据引用、只读角色和可达网络时使用。不要在命令参数或 SQL 中写密码。

```sh
PGOPTIONS='-c default_transaction_read_only=on -c statement_timeout=600000 -c lock_timeout=5000' psql -X -v ON_ERROR_STOP=1 -P pager=off -f ontology/example/queries/q01.sql
```

SQL 文件恰好一个查询语句，禁止 psql 元命令、COPY、DO、SET、多语句、调用有副作用函数。元数据用 information_schema、pg_catalog、obj_description/col_description；按已授权 schema 过滤。EXPLAIN 不加 ANALYZE，目录统计仅作估算，真实计数仍需查询。连接参数可由用户授权提供并通过 connect_database 建立只读连接，或由管理员预配置；不从平台控制数据库或其他用户配置中获取凭据。

## MySQL

需要 mysql 客户端、管理员配置的只读账号和 defaults-extra-file/login-path，秘密文件不得在可归档工作区。先验证服务端版本与实际只读状态、语句超时支持，不照搬未经验证的多赋值 init-command。元数据使用限定 TABLE_SCHEMA 的 information_schema.TABLES/COLUMNS/KEY_COLUMN_USAGE；注意 collation 与布尔编码。缺少服务器端超时或受控查询工具时报告连接待适配，不依赖取消客户端保证服务器查询终止。

## 达梦 DM

小树的 dmq 是其私有运行时能力，本包不包含它。目标环境有 database-readonly MCP 时，直接用 connect_database(engine="dameng", host, port, username, password, schema) 接收用户授权连接信息；返回临时 source_id 后再探查和查询。不要把仅支持预置 ID 的旧限制继续套用到新版工具；确实缺少工具或连通失败时说明具体缺项。探查使用按 OWNER 限定的 ALL_TABLES、ALL_TAB_COMMENTS、ALL_COL_COMMENTS 等；不能套用 information_schema。不得用裸 dmPython 绕过受控工具。

## 成本、证据与权限

一次只执行一个 SQL，最长 600 秒；不使用 for 循环把多条业务查询藏在一次执行中。可独立并发少量轻查询，不用子智能体。大表全扫、DISTINCT、跨表归并、JSON 展开先看规模、索引与计划；LIMIT 不一定减少聚合成本，count(*) 也不保证便宜。拆批后检查区间覆盖和去重，采样必须标注方法与非全量。外部数据库的硬权限由只读账号/MCP 实施，Skill 文字和辅助脚本不能替代授权隔离。
