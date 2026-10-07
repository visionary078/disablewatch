# 软件

| 路径 | 内容 |
|---|---|
| [miniprogram/](miniprogram) | 微信小程序。开发者工具打开这个目录。仍用手机自己的摄像头 |
| [android/](android/README.md) | 手机本地程序。WiFi 摄像头把图送到这里，再调用网关，短句回眼镜 |
| [server/](server/README.md) | 识别网关。`/glasses/` 仍是本机摄像头演示 |
| [DEPLOY.md](DEPLOY.md) | 部署 |
| [docs/看见下一步_软件技术文档_v1.7.md](docs/看见下一步_软件技术文档_v1.7.md) | 软件技术文档 v1.7 |
| [docs/看见下一步_当前判定说明.md](docs/看见下一步_当前判定说明.md) | 当前识别、记忆和两端页面的判定 |

本地测试（在 `server` 目录）：

```powershell
python -m unittest discover -s tests -v
```

改 `server/` 的 Pull Request 会在 GitHub 上跑同一组测试。密钥放在 `server/.env`，不要提交。
