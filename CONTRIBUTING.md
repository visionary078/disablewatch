# 一起改

完整规则在 [docs/开发与代码提交规范.md](docs/开发与代码提交规范.md)。这里是每天会用到的几条。

从最新的 `main` 拉一条短分支，一两天内开 Draft PR。不要建个人长期分支。

| 类型 | 例子 |
|---|---|
| 硬件 | `feat/hardware-短说明` |
| 软件 | `feat/software-短说明` |
| 修复 | `fix/短说明` |
| 文档 | `docs/短说明` |

只暂存这次的路径。不要 `git add .`。不要提交 `.env`、密钥和虚拟环境。

合并前：

1. 改了 `software/server/`，跑 `python -m unittest discover -s tests -v`。
2. 改了硬件的重量、充电或佩戴，附上机记录；没有样机就写「未上机」。
3. 项目负责人看过再合并进 `main`。

邀请队友时给 **Write**，不要给 Admin。
