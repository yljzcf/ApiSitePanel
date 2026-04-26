# 007_geminiTest

一个用于抓取并整理自己正在使用的 API 站点模型、分组与计费配置的小脚本，便于横向对比不同站点的模型可用性与使用成本。

## 功能范围

当前入口文件是 [main.py](main.py)，负责：

- 读取项目根 `site.json`
- 交互式新增站点配置
- 调用 [script/](script/) 下的抓取与清洗逻辑
- 输出抓取进度、失败原因与汇总结果
- 在一轮结束后继续驻留终端，支持继续新增站点或直接回车退出
- 在抓取失败时提示对应 `raw/` 目录，允许手动补充 JSON 后重新清洗

## 公开仓库包含内容

此仓库只保留运行脚本所需的最小公开文件：

- [main.py](main.py)
- [script/](script/)
- [site.json.example](site.json.example)
- [README.md](README.md)
- [.gitignore](.gitignore)
- [requirements.txt](requirements.txt)
- [LICENSE](LICENSE)

仓库**不包含**真实配置、真实抓取数据、工作日志、内部文档和测试文件。

## 运行环境

- 推荐 Python：3.12
- 最低支持版本：Python 3.10

安装依赖：

```bash
python -m pip install -r requirements.txt
```

## 配置方式

1. 复制 [site.json.example](site.json.example) 为 `site.json`。
2. 按需填写你自己有权访问的站点配置。

示例：

```json
[
  {
    "url": "https://api.example.com",
    "name": "example-site"
  }
]
```

字段说明：

- `url`：站点根地址，必须包含 `http://` 或 `https://`
- `name`：站点显示名，同时会用于输出目录命名

程序在新增站点时会基于规范化后的 URL 查重，避免写入重复站点。

## 运行方式

在项目根执行：

```bash
python main.py
```

程序会依次：

1. 检查并读取 `site.json`
2. 询问是否直接使用现有配置
3. 在需要时进入交互式新增站点流程
4. 尝试抓取站点的模型和分组相关 JSON 数据
5. 清洗并输出站点汇总结果
6. 对抓取失败站点提示手动补 `raw/` 后重试清洗
7. 保持终端驻留，等待继续新增或退出

## 输出说明

运行后会在项目根生成本地输出目录：

- `site/<站点名>/raw/`：抓取得到的原始 JSON
- `site/<站点名>/<站点名>.json`：清洗后的结果

其中 `pricing.json` 是清洗阶段的必需输入；如果站点还存在分组信息，程序会同时读取 `user_groups.json` 参与整理。

## 合法使用与免责声明

本项目仅面向**个人使用**，仅适用于你本人拥有、正在使用或已获得明确授权访问的 API 站点。

使用本项目时，你需要自行确保：

- 已获得目标站点的访问授权
- 遵守目标站点的服务条款、速率限制和使用规则
- 遵守当地适用法律、数据合规与隐私要求
- 不将本项目用于未授权抓取、批量滥用或其他违规用途

仓库不提供任何真实凭证、真实站点配置或真实抓取结果。因使用者违反服务条款、授权边界或法律法规所产生的后果，由使用者自行承担。

## License

本项目采用 [MIT License](LICENSE)。