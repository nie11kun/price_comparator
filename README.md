# App 订阅价格比较器

## 描述

本项目旨在抓取并展示选定应用程序（目前包括 iCloud+, ChatGPT, Claude, Google One）在不同 Apple App Store 地区以及 Apple 支持网站上的订阅价格。它获取本地货币价格，尝试使用外部 API（并带有内置备用方案）将其转换为人民币 (CNY)，并在一个简单的 Web 界面上按估算的 CNY 价格排序显示所有可用的结果。

主要目标是为不同地理位置的订阅成本提供一个比较参考点。

**数据来源:**
* **iCloud+:** 价格从详细说明区域定价的 Apple 官方支持页面抓取 ([https://support.apple.com/en-us/108047](https://support.apple.com/en-us/108047))。
* **ChatGPT, Claude, Google One:** 价格直接从相应应用程序在区域性 Apple App Store 网站上的页面抓取 (例如, `https://apps.apple.com/us/app/...`)。

**免责声明:** 网页抓取，特别是像 App Store 这样的动态网站，本质上是脆弱的，并可能违反目标网站的服务条款。本工具仅供参考，请负责任地使用。价格准确性取决于抓取的成功率、频率以及汇率数据的可靠性。

## 功能特性

* 抓取 iCloud+, ChatGPT, Claude, 和 Google One 的价格。
* 尝试从一个可配置的 App Store 地区列表 (`TARGET_REGIONS`) 获取数据。
* 使用特定的 CSS 类选择器来抓取 App Store 应用内购买项 (比依赖标题文本更稳定)。
* 解析 Apple 支持页面上的结构化数据以获取 iCloud+ 定价。
* 使用外部汇率 API 将获取的本地价格转换为 CNY。
* 包含在 API 失败时回退到硬编码汇率的机制。
* 为汇率 API 实现了一个简单的熔断器机制，以防止在更新周期内因失败而重复调用。
* 提供一个基于 Flask 的后端 API (`/api/prices`) 来提供数据。
* 在 Web 前端按估算的 CNY 价格排序显示所有可用的价格结果。
* 包含一个调度器 (`APScheduler`) 用于自动后台更新价格数据。
* 允许通过特定路由 (`/admin/trigger-update`) 手动触发更新过程。
* (已实现) 在数据库插入前过滤掉来自指定地区（例如 EG, PH）的数据。

## 技术栈

* **后端:** Python 3.8+, Flask
* **数据抓取:** `requests`, `beautifulsoup4`
* **数据库:** PostgreSQL (需要服务器安装)
* **数据库驱动:** `psycopg2-binary` (可通过更改驱动和 `DATABASE_URL` 适配其他数据库，如 MySQL)
* **调度:** `APScheduler`
* **环境变量:** `python-dotenv`
* **前端:** HTML, CSS, JavaScript
* **汇率数据:** 需要来自第三方服务（如 [ExchangeRate-API.com](https://www.exchangerate-api.com/) 或类似服务）的 API 密钥。
* **部署 (生产环境):** 推荐: Gunicorn, Nginx (或类似的 WSGI/Web 服务器配置)

## 安装设置指南

1.  **先决条件:**
    * 已安装 Python 3.8 或更高版本。
    * 已安装 `pip` (Python 包安装器)。
    * 已安装并运行 PostgreSQL 服务器。
    * Git (可选, 用于克隆)。

2.  **克隆仓库 (如果适用):**
    ```bash
    git clone <your-repository-url>
    cd price-comparator
    ```
    *如果没有仓库，请创建一个名为 `price-comparator` 的目录并将代码文件放入其中。*

3.  **创建并激活虚拟环境:**
    * 导航到项目目录 (`price-comparator`)。
    * **Windows:**
        ```bash
        python -m venv venv
        .\venv\Scripts\activate
        ```
    * **macOS / Linux:**
        ```bash
        python3 -m venv venv
        source venv/bin/activate
        ```
    * 你应该能在终端提示符前看到 `(venv)`。

4.  **安装依赖:**
    ```bash
    pip install -r requirements.txt
    ```

5.  **数据库设置 (PostgreSQL):**
    * 确保你的 PostgreSQL 服务器正在运行。
    * 使用 `psql` 连接到 PostgreSQL (初始可能需要以默认的 `postgres` 超级用户连接)。
    * 创建数据库、用户并授予权限：
        ```sql
        CREATE DATABASE price_db;
        -- 选择一个强密码!
        CREATE USER price_user WITH PASSWORD 'your_strong_password';
        -- 授予连接权限
        GRANT ALL PRIVILEGES ON DATABASE price_db TO price_user;
        -- 以超级用户连接到新数据库以授予模式权限
        \c price_db
        -- 授予应用用户模式使用和创建权限
        GRANT USAGE ON SCHEMA public TO price_user;
        GRANT CREATE ON SCHEMA public TO price_user;
        \q -- 暂时退出 psql
        ```
    * **以 `price_user` 身份连接** 来创建表 (确保所有权)：
        ```bash
        # 使用正确的连接字符串或 psql 参数
        psql postgresql://price_user:your_strong_password@localhost:5432/price_db
        ```
    * 在 `psql` 中 (以 `price_user` 连接到 `price_db` 后)，创建 `prices` 表：
        ```sql
        CREATE TABLE prices (
            id SERIAL PRIMARY KEY,
            app_name VARCHAR(100) NOT NULL,
            plan_name VARCHAR(150),
            region VARCHAR(10) NOT NULL, -- 例如 'US', 'CN', 'GB'
            currency VARCHAR(5) NOT NULL, -- 例如 'USD', 'CNY'
            price NUMERIC(10, 2) NOT NULL, -- 本地价格
            price_cny NUMERIC(10, 2),      -- 转换为 CNY 的价格
            last_updated TIMESTAMPTZ NOT NULL -- 带时区的时间戳
        );

        -- 可选: 创建索引
        CREATE INDEX idx_app_plan_region ON prices (app_name, plan_name, region);

        -- 再次授予必要权限 (虽然所有者应该已有)
        GRANT ALL PRIVILEGES ON TABLE prices TO price_user;
        GRANT USAGE, SELECT ON SEQUENCE prices_id_seq TO price_user;

        -- 验证表创建
        \dt prices
        \q -- 退出 psql
        ```

6.  **环境变量:**
    * 在项目根目录 (`price-comparator`) 创建一个名为 `.env` 的文件。
    * 添加以下内容，**将占位符替换为你的实际值**:
        ```dotenv
        # .env - 保持此文件安全! 如果公开使用 Git，请添加到 .gitignore。

        # 替换为你的实际 PostgreSQL 连接字符串
        DATABASE_URL=postgresql://price_user:your_strong_password@localhost:5432/price_db

        # 从汇率服务获取 API 密钥 (例如 exchangerate-api.com)
        EXCHANGE_RATE_API_KEY=YOUR_EXCHANGE_RATE_API_KEY_HERE
        ```
    * **(可选但推荐):** 在项目根目录创建一个 `.gitignore` 文件，并将 `.env` 和 `venv/` 添加进去。

7.  **汇率 API 密钥:**
    * 在一个汇率提供商（例如 [ExchangeRate-API.com](https://www.exchangerate-api.com/)）注册免费或付费计划。
    * 获取你的 API 密钥并将其粘贴到 `.env` 文件中的 `EXCHANGE_RATE_API_KEY` 变量处。

8.  **更新备用汇率:**
    * 打开 `app.py` 文件。
    * 找到 `FALLBACK_RATES_TO_CNY` 字典。
    * 检查硬编码的汇率，并**定期用当前近似值更新它们**，以提高备用方案的准确性。在注释中记录你最后更新的日期。

## 运行应用程序

1.  **初始数据填充:**
    * 数据库初始为空。你需要至少运行一次抓取和更新过程。
    * 启动 Flask 应用 (见下一步)。
    * 运行后，打开一个**单独的终端**或使用 `curl`、Postman 等工具，通过向 `/admin/trigger-update` 端点发送请求 (POST 或 GET，取决于你的路由定义) 来手动触发更新：
        ```bash
        # 使用 curl 的示例 (如果路由允许 GET 测试)
        curl [http://127.0.0.1:5000/admin/trigger-update](http://127.0.0.1:5000/admin/trigger-update)
        # 或使用 POST
        curl -X POST [http://127.0.0.1:5000/admin/trigger-update](http://127.0.0.1:5000/admin/trigger-update)
        ```
    * **监控运行 `app.py` 的终端。** 更新过程需要几分钟。观察日志，查看抓取、转换和数据库插入过程中的成功或错误信息。
    * 更新完成后，数据应该已存入数据库。

2.  **运行开发服务器:**
    * 确保你的虚拟环境已激活 (`source venv/bin/activate` 或 `.\venv\Scripts\activate`)。
    * 确保你的 PostgreSQL 服务器正在运行。
    * 运行 Flask 应用：
        ```bash
        flask run
        # 或
        python app.py
        ```
    * 打开你的网络浏览器并访问 `http://127.0.0.1:5000` (或 Flask 提供的地址)。

3.  **自动更新:**
    * `app.py` 中配置的 `APScheduler` 应该会按设定的间隔（例如每 6 小时）在后台自动运行 `update_prices_in_db` 函数。监控日志以确认调度器正在运行。

4.  **生产环境部署 (概念):**
    * **不要**在生产环境中使用 Flask 开发服务器 (`flask run` 或 `app.run(debug=True)`)。
    * 使用生产级的 WSGI 服务器，如 `Gunicorn`:
        ```bash
        # 示例: 在 8000 端口用 4 个工作进程运行
        gunicorn --workers 4 --bind 0.0.0.0:8000 app:app
        ```
    * 在 Gunicorn 前面使用反向代理，如 `Nginx`，来处理入站请求、直接提供静态文件 (`static/`) 以及管理 HTTPS。
    * 使用进程管理器，如 `systemd`，来管理 Gunicorn 进程 (确保可靠运行并在失败时重启)。
    * 在服务器上安全地设置环境变量 (`DATABASE_URL`, `EXCHANGE_RATE_API_KEY`) – **不要**在生产环境中使用 `.env` 文件。

## 项目结构 (示例)

```
price-comparator/
├── venv/                  # 虚拟环境目录
├── static/                # 静态文件 (CSS, JS)
│   ├── style.css
│   └── script.js
├── templates/             # HTML 模板
│   └── index.html
├── .env                   # 环境变量 (DATABASE_URL, API_KEY) - 不要提交!
├── .gitignore             # Git 忽略文件 (应包含 .env, venv/)
├── app.py                 # 主 Flask 应用, API 逻辑, 抓取逻辑
└── requirements.txt       # Python 依赖
```

## 重要注意事项

* **抓取器的脆弱性:** Apple 经常更新其网站结构。`scrape_icloud_prices` 和 `scrape_app_store_price` 中使用的 CSS 选择器最终**会失效**。你**必须**定期检查抓取是否正常工作，并通过检查实时页面来更新选择器。
* **数据准确性与时效性:** 显示的价格仅与 `update_prices_in_db` 任务最后一次成功运行的时间一样新。它们并非真正的实时数据。CNY 转换依赖于外部 API 或可能过时的备用汇率。
* **应用可用性:** 并非所有应用都在所有被抓取的地区可用。抓取器会处理 404 错误，但在应用不存在的地方无法获取数据。
* **API 密钥与限制:** 保持你的 `EXCHANGE_RATE_API_KEY` 安全。注意 API 提供商施加的任何使用限制，特别是免费套餐。熔断器有助于减少失败期间的过度调用。
* **服务条款:** 网页抓取可能违反 Apple 的 App Store 和网站服务条款。请负责任地使用本工具，风险自负。频繁、激进的抓取可能导致 IP 被封锁。考虑在 App Store 抓取循环中添加延时 (`time.sleep`)。
* **数据过滤:** 当前 `update_prices_in_db` 函数包含逻辑，会在插入数据库前过滤掉来自埃及 (EG) 和菲律宾 (PH) 的 iCloud+ 数据。如果需要显示这些地区的数据，请移除或修改该过滤逻辑。

