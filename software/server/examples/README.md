# 固定测试素材

请将 10 张现场拍摄或获准使用的测试图片放在本目录，文件名必须与 `cases.json` 一致：

```text
entrance_01.jpg
entrance_02.jpg
product_01.jpg
product_02.jpg
price_01.jpg
price_02.jpg
cashier_01.jpg
cashier_02.jpg
obstacle_01.jpg
obstacle_02.jpg
```

拍摄要求：

- 每类准备一张目标清晰图和一张遮挡、反光或复杂背景图。
- 避免拍到可识别的人脸、支付码、手机号和其他个人信息。
- 保留原始图片，不用标注框引导模型。
- 在真实用户测试前，这些图片只能用于工程验证和演示排练。

批量测试：

```bash
python3 benchmark.py --profile primary
```
