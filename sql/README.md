数据库初始化由项目根目录的 `init_db.py` 完成。

执行：

```powershell
python init_db.py
```

脚本会创建 `SFweb` 数据库，优先从 `smart_fishery_db` 迁移已有数据，再补齐 Web 端需要的告警、阈值、AI 识别、自动控制和财务流水。
