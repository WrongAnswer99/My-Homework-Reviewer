# 本地 OCR 运行包

从 `F:\projects\OCRtest` 复制现有接口、运行库及 **PP-OCRv4 mobile / PP-OCRv6 tiny** 模型；本项目运行时不读取原项目，也不需要重新编译。默认使用 v4。

| 本地文件 | 来源 |
| --- | --- |
| `ocr.dll`、`onnxruntime.dll` | `OCRtest/build/` |
| `ocr_api.hpp` | `OCRtest/src/ocr_api.hpp`，原始 C ABI 声明 |
| `ocr.hpp` | `OCRtest/sdk/ocr.hpp`，原始 C++ SDK 封装 |
| `models/onnx/v6_det_tiny.onnx` | `OCRtest/models/onnx/`，检测模型 |
| `models/onnx/v6_rec_tiny.onnx` | `OCRtest/models/onnx/`，识别模型 |
| `models/onnx/v6_tiny_dict.txt` | `OCRtest/models/onnx/`，识别字典 |
| `models/onnx/ch_PP-OCRv4_det_mobile.onnx` | `OCRtest/models/onnx/`，v4 检测模型 |
| `models/onnx/ch_PP-OCRv4_rec_mobile.onnx` | `OCRtest/models/onnx/`，v4 识别模型 |
| `models/onnx/ppocr_keys_v1.txt` | `OCRtest/models/onnx/`，v4 配套字典 |
| `libgcc_s_seh-1.dll`、`libstdc++-6.dll`、`libwinpthread-1.dll` | 原项目使用的本机 MinGW 工具链运行库 |

`engine.py` 通过 Python `ctypes` 调用 `ocr_create`、`ocr_run_bgr` 和 `ocr_destroy`。v4 使用归一化参数 `norm=0`，v6 tiny 使用 `norm=2`；模型、字典和参数一起切换。默认 4 个 CPU 线程，传入本地模型绝对路径。图像转换为连续 BGR 像素后在内存中识别。

`question_ocr.py` 用归档的原字体渲染整道题，包含题干、选项及原图片，一张图调用一次 OCR。识别结果按区域位置归入题干和选项，直接输出文字；没有逐字对应表或单字补识别。短文本加上题干/选项标签以帮助检测，导出时去掉标签。识别输入 PNG 保留在导出目录的 `ocr_images/`，可直接核对原文字形。

已删除旧的 `font_decoder.py`；不读取或生成 `font_map.json`、`ocr_cache.json`。Windows 的微软雅黑用于绘制普通文字，归档字体绘制加密文字。字体自身的 cmap 仅用于选择绘图字体，并不把 OCR 结果映射回原 Unicode。

DLL 为 Windows x64 版本，需要 64 位 Python。运行库已放在 DLL 同目录，接口显式设置 DLL 搜索目录并预加载附带的 ONNX Runtime，不依赖系统 PATH。

直接识别其他本地图片：

```python
from PIL import Image
from ocr.engine import Engine

with Image.open("图片.png") as image, Engine(model='v4') as engine:
    for line in engine.recognize(image):
        print(line.text, line.score, line.box)
```
