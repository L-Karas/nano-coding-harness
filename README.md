## 环境搭建

```bash
uv sync
```

## 模型配置

在根目录创建 `.env` 文件，内容模板：

```
OPENAI_API_KEY=[your openai api key]
OPENAI_BASE_URL=[base url]

MODEL=[main loop model name]
SUB_MODEL=[sub-agent and teammate model name]
FALLBACK_MODEL=[fallback model name]
```

## 测试

```bash
uv run main.py
```

## 功能支持
