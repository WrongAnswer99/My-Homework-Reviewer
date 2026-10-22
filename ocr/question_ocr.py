"""Render complete questions and return OCR text, without character mappings."""
from __future__ import annotations

import io
import os
import re
from pathlib import Path

from fontTools.ttLib import TTFont
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .engine import Engine


def render_question(blob, pieces, size=40):
    normal = ImageFont.truetype(str(Path(os.environ.get('WINDIR', 'C:/Windows')) /
                                    'Fonts/msyh.ttc'), size)
    embedded = ImageFont.truetype(io.BytesIO(blob), size)
    with TTFont(io.BytesIO(blob)) as font:
        supported = set(font.getBestCmap())
    width, margin, gap = size * 34, size // 2, size * 2
    blocks, y = [], margin
    for index, piece in enumerate(pieces):
        if 'runs' not in piece:
            if piece.get('image_path'):
                try:
                    with Image.open(piece['image_path']) as original:
                        picture = original.convert('RGB')
                    scale = min(1, (width - margin * 2) / picture.width)
                    picture = picture.resize((max(1, round(picture.width * scale)),
                                              max(1, round(picture.height * scale))))
                    blocks.append(('image', picture, y))
                    y += picture.height + margin
                except (OSError, UnidentifiedImageError):
                    # SVG and missing resources remain intact in Markdown.
                    pass
            elif piece.get('display'):
                blocks.append(('literal', piece['display'], y))
                y += gap
            continue
        rows, row, x = [], [], margin
        prefix = piece['prefix']
        runs = [(re.sub(r'\s+', ' ', text), secret) for text, secret in piece['runs']]
        if runs:
            runs[0] = (runs[0][0].lstrip(), runs[0][1])
            runs[-1] = (runs[-1][0].rstrip(), runs[-1][1])
        for text, encrypted in [(prefix, False), *runs]:
            for char in text:
                if char in '\r\n':
                    if row:
                        rows.append(row)
                    row, x = [], margin
                    continue
                char = ' ' if char.isspace() else char
                face = embedded if encrypted and ord(char) in supported else normal
                advance = max(1, round(face.getlength(char)))
                if row and x + advance > width - margin:
                    rows.append(row)
                    row, x = [], margin
                row.append((char, face, advance))
                x += advance
        if row:
            rows.append(row)
        start = y
        blocks.append(('text', rows, y))
        y += len(rows) * gap
        piece['bounds'] = (start, y)
        piece['index'] = index
        y += margin
    image = Image.new('RGB', (width, max(size * 3, y + margin)), 'white')
    draw = ImageDraw.Draw(image)
    for kind, content, top in blocks:
        if kind == 'image':
            image.paste(content, (margin, top))
        elif kind == 'literal':
            draw.text((margin, top + size * 1.5), content, font=normal,
                      fill='black', anchor='ls')
        else:
            for row_index, row in enumerate(content):
                x = margin
                for char, face, advance in row:
                    draw.text((x, top + row_index * gap + size * 1.5), char,
                              font=face, fill='black', anchor='ls')
                    x += advance
    return image


class QuestionOCR:
    def __init__(self, model='v4'):
        if model not in ('v4', 'v6tiny'):
            raise ValueError(f'不支持的 OCR 模型：{model}')
        self.model = model
        self.engine = None
        self.question_count = 0

    def recognize(self, blob, pieces, image_path):
        image = render_question(blob, pieces)
        image_path.parent.mkdir(parents=True, exist_ok=True)
        image.save(image_path)
        if self.engine is None:
            self.engine = Engine(model=self.model)
        self.question_count += 1
        lines = self.engine.recognize(image)
        for piece in pieces:
            if 'runs' not in piece:
                continue
            if not piece['encrypted']:
                piece['result'] = piece['original']
                continue
            top, bottom = piece['bounds']
            selected = [line for line in lines
                        if top <= sum(y for _, y in line.box) / len(line.box) < bottom]
            # Each question is one OCR call. Geometry only restores paragraphs
            # and choices; there is no correspondence between source characters
            # and recognized characters, nor any single-character fallback.
            selected.sort(key=lambda line: (round(min(y for _, y in line.box) / 40),
                                            min(x for x, _ in line.box)))
            text = ''.join(line.text.strip() for line in selected)
            prefix = piece['prefix'].rstrip('：:')
            match = re.match(re.escape(prefix) + r'\s*[:：]\s*', text)
            if not match:
                # The known paragraph label can itself suffer OCR errors; its
                # colon and fixed short position identify it without matching
                # any source/recognized characters in the question body.
                match = re.match(r'^.{1,' + str(len(prefix) + 1) + r'}?[:：]\s*', text)
            if not match or not text[match.end():].strip():
                raise ValueError(f'{self.model} 未能识别整题中的{prefix}文字，'
                                 '请尝试另一 OCR 模型或重新保存页面。')
            piece['result'] = text[match.end():]
        return pieces

    def close(self):
        if self.engine is not None:
            self.engine.close()
            self.engine = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
