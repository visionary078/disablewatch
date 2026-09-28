# 看见下一步

视障辅助：微信小程序和眼镜页负责看、听、说；眼镜硬件负责把摄像头放到眼前。识别仍走同一套云端网关。

`main` 里同时放硬件和软件。改东西从 `main` 拉分支，开 Pull Request，仓库主人看过再合并。

## 目录

| 路径 | 内容 |
|---|---|
| [hardware/](hardware/README.md) | 眼镜方案、Word 策划案、上机检查 |
| [software/miniprogram/](software/miniprogram) | 微信小程序。用开发者工具打开这个文件夹 |
| [software/server/](software/server/README.md) | FastAPI 识别服务，含眼镜页 |
| [software/DEPLOY.md](software/DEPLOY.md) | 服务器部署 |
| [CONTRIBUTING.md](CONTRIBUTING.md) | 怎么拉分支、怎么合并 |

## 检查

- 软件：改 `software/` 的 Pull Request 会跑自动测试（见 `.github/workflows/software-tests.yml`）。本地在 `software/server` 里执行 `python -m unittest discover -s tests`。
- 硬件：实物或打印件按 [hardware/tests/bringup-checklist.md](hardware/tests/bringup-checklist.md) 逐项记。没有样机时，在 PR 里写明「只改文档，未上机」。

密钥不进仓库。把 `software/server/.env.example` 复制成 `.env` 再填。
