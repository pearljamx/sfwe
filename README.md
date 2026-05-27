# 智慧渔场物联网管控系统 Web 端

Flask + MySQL 的智慧渔场 Web 端。页面正式读取 `SFweb` 数据库，不默认使用静态数据。

## 启动

```powershell
python -m pip install -r requirements.txt
copy .env.example .env
python init_db.py
python app.py
```

访问 `http://127.0.0.1:5000`。

首次运行前先编辑 `.env`，把 MySQL 配置改成当前电脑自己的值：

```dotenv
MYSQL_HOST=127.0.0.1
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=你的MySQL密码
MYSQL_DATABASE=SFweb
```

如果对方的数据库名不是 `SFweb`，可以把 `MYSQL_DATABASE` 改成自己的库名，例如 `MYSQL_DATABASE=my_fishery`。执行 `python init_db.py` 时会按 `.env` 里的数据库名创建并初始化。

如果页面提示 `Access denied for user`，通常是 `.env` 里的 `MYSQL_USER` 或 `MYSQL_PASSWORD` 和当前电脑 MySQL 不一致。先确认能在命令行登录 MySQL，再重新执行：

```powershell
python init_db.py
python app.py
```

如果页面仍显示旧提示“请先执行 python init_db.py 创建并初始化 SFweb 数据库”，说明当前运行的不是新版目录，或者旧的 `python app.py` 进程还没关闭。请先关闭正在运行的窗口，重新解压新版压缩包，进入新版 `SFweb` 目录后再执行启动命令。

如果页面提示 `cryptography package is required for sha256_password or caching_sha2_password auth methods`，说明当前 MySQL 账号使用了 MySQL 8 的认证方式，但 Python 环境缺少 `cryptography` 依赖。请重新执行：

```powershell
python -m pip install -r requirements.txt
python init_db.py
python app.py
```

## 演示账号

- 管理员：`admin / admin123`
- 运维员：`operator / operator123`
- 供应商：`supplier1 / supplier123`
- 供应商：`supplier2 / supplier123`

## 页面范围

管理员端：运营总览、鱼池监控、设备调度、告警中心、AI 鱼病识别、自动控制、数据分析。

供应商端：供应商看板、产品库存、订单协同、财务统计。

## 数据库

`init_db.py` 会创建 `.env` 里 `MYSQL_DATABASE` 指定的数据库，优先从已有 `smart_fishery_db` 迁移鱼池、设备、传感器、供应商、产品和订单数据，再补齐系统运行需要的阈值、告警、AI 识别、自动控制策略和财务流水。

## UDP 与 AI 鱼病识别

水质数据使用 `DATA:` UDP 包，图片使用 `IMG:START / IMG:CHUNK / IMG:END` 分片协议，默认监听 `0.0.0.0:8080`。图片重组后保存到 `static/uploads/ai/`，模型存在时写入 `ai_diagnoses` 并联动 `ai_disease` 告警。

训练入口：

```powershell
python train_fish_disease.py doctor
python train_fish_disease.py prepare-cls
python train_fish_disease.py train-cls
```

检测模型需要 Roboflow key：

```powershell
$env:ROBOFLOW_API_KEY="你的key"
python train_fish_disease.py prepare-det
python train_fish_disease.py train-det
```
