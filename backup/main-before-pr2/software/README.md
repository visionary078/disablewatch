# 软件

| 路径 | 内容 |
|---|---|
| [miniprogram/](miniprogram) | 微信小程序。开发者工具打开这个目录 |
| [server/](server/README.md) | 识别网关和眼镜页 |
| [DEPLOY.md](DEPLOY.md) | 部署 |

本地测试（在 `server` 目录）：

```powershell
python -m unittest discover -s tests -v
```

改 `server/` 的 Pull Request 会在 GitHub 上跑同一组测试。密钥放在 `server/.env`，不要提交。
