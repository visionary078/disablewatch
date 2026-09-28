# Glasses

看见下一步的眼镜方案都放在这个文件夹。正文仍是中文，文件名用英文。

| 文件 | 内容 |
|---|---|
| [hardware-plan-v1.md](hardware-plan-v1.md) | 3D 打印眼镜硬件策划：重量、充电、对标、三期 |
| [customer-talking-points-v1.md](customer-talking-points-v1.md) | 客户交流话术 |
| [neckband-plan-v1.md](neckband-plan-v1.md) | 挂脖眼镜详细方案 |
| [see-next-step-glasses-plan-v1.docx](see-next-step-glasses-plan-v1.docx) | 完整 Word 策划案 v1.1（含可换多模态、MemOS） |
| [memos-long-memory-plan-v1.md](memos-long-memory-plan-v1.md) | 与记忆张量 MemOS 合并：长期记忆层 |
| [tests/bringup-checklist.md](tests/bringup-checklist.md) | 样机上机检查。软件自动测试在仓库的 GitHub Actions |

相关代码在 `software/`：

- 网页：`software/server/static/glasses/`
- 打开识别：`http://127.0.0.1:8000/glasses/` 或 `https://watchapi.divesee.com/glasses/`
- 造型展示：`http://127.0.0.1:8000/glasses/look.html`
