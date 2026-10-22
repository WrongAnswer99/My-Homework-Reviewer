"""Call the copied OCRtest C ABI with in-memory pixels (cdecl, Windows x64)."""
from __future__ import annotations

import ctypes as ct
import os
from dataclasses import dataclass
from pathlib import Path


class ApiLine(ct.Structure):
    _fields_ = [('text', ct.c_char * 512), ('score', ct.c_double),
                ('quad_x', ct.c_double * 4), ('quad_y', ct.c_double * 4),
                ('cx', ct.c_int), ('cy', ct.c_int), ('w', ct.c_int), ('h', ct.c_int)]


@dataclass(frozen=True)
class TextLine:
    text: str
    score: float
    box: tuple


class Engine:
    MODELS = {
        'v6tiny': ('v6_det_tiny.onnx', 'v6_rec_tiny.onnx', 'v6_tiny_dict.txt', 2),
        'v4': ('ch_PP-OCRv4_det_mobile.onnx', 'ch_PP-OCRv4_rec_mobile.onnx',
               'ppocr_keys_v1.txt', 0),
    }

    def __init__(self, directory=None, threads=4, model='v4'):
        if model not in self.MODELS:
            raise ValueError(f'不支持的 OCR 模型：{model}')
        self.model = model
        if os.name != 'nt' or ct.sizeof(ct.c_void_p) != 8:
            raise OSError('本项目的 OCR DLL 需要 Windows 和 64 位 Python。')
        self.directory = Path(directory or Path(__file__).parent).resolve()
        self.handle = None
        self.search_path = None
        self.runtime = None
        self.dll = None
        model_dir = self.directory / 'models' / 'onnx'
        det, rec, dictionary, normalization = self.MODELS[model]
        files = [self.directory/'ocr.dll', self.directory/'onnxruntime.dll',
                 model_dir/det, model_dir/rec, model_dir/dictionary]
        for path in files:
            if not path.is_file():
                raise OSError(f'缺少 OCR 文件：{path}')
        try:
            self.search_path = os.add_dll_directory(str(self.directory))
            # OCRtest's runtime uses LoadLibraryA("onnxruntime.dll"). Preload
            # this exact copied DLL so inference never depends on the cwd/PATH.
            self.runtime = ct.CDLL(str(files[1]), winmode=0x1100)
            self.dll = ct.CDLL(str(files[0]), winmode=0x1100)
            self.dll.ocr_create.argtypes = [ct.c_char_p, ct.c_char_p, ct.c_char_p,
                                           ct.c_int, ct.c_int, ct.c_int, ct.c_double,
                                           ct.c_char_p, ct.c_int]
            self.dll.ocr_create.restype = ct.c_void_p
            self.dll.ocr_destroy.argtypes = [ct.c_void_p]
            self.dll.ocr_destroy.restype = None
            self.dll.ocr_run_bgr.argtypes = [ct.c_void_p, ct.POINTER(ct.c_uint8),
                                            ct.c_int, ct.c_int, ct.POINTER(ApiLine),
                                            ct.c_int, ct.c_char_p, ct.c_int]
            self.dll.ocr_run_bgr.restype = ct.c_int
            error = ct.create_string_buffer(2048)
            self.handle = self.dll.ocr_create(
                *(str(p).encode('utf-8') for p in files[2:]),
                normalization, threads, 0, 0.01, error, len(error))
            if not self.handle:
                raise OSError('OCR 模型加载失败：' + error.value.decode('utf-8', errors='replace'))
        except BaseException:
            self.close()
            raise

    def recognize(self, image):
        if not self.handle:
            raise OSError('OCR 引擎已关闭。')
        rgb = image.convert('RGB')
        pixels = (ct.c_uint8 * (rgb.width * rgb.height * 3)).from_buffer_copy(
            rgb.tobytes('raw', 'BGR'))
        capacity = 256
        while True:
            lines = (ApiLine * capacity)()
            error = ct.create_string_buffer(2048)
            count = self.dll.ocr_run_bgr(self.handle, pixels, rgb.width, rgb.height,
                                       lines, capacity, error, len(error))
            if count < 0:
                raise OSError('OCR 识别失败：' + error.value.decode('utf-8', errors='replace'))
            # The original ABI returns total line count, even when cap is small.
            if count <= capacity:
                break
            capacity = count
        return [TextLine(line.text.decode('utf-8'), line.score,
                         tuple(zip(line.quad_x, line.quad_y))) for line in lines[:count]]

    def close(self):
        if self.handle:
            self.dll.ocr_destroy(self.handle)
            self.handle = None
        if self.search_path is not None:
            self.search_path.close()
            self.search_path = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
