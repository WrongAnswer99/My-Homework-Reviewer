"""Local, editable subject prompts; answer block markers stay program-generated."""
from __future__ import annotations

import json
import uuid
from pathlib import Path

DEFAULT_CONFIG = Path(__file__).resolve().parent/'data/prompt_templates.json'

ANALOG_PROMPT = '''请解答我随后发送的这张电路、模拟电子技术或数字电子技术题目图片。

请逐题仔细读取题干、图中的连接关系、元件参数、极性和选项。先判断适用模型和条件，再推导计算；填写单位并检查符号、量纲和结果是否符合电路工作状态。选择题给出选项及理由，判断题给出判断及理由，填空题按空的顺序逐项作答。需要假设时明确写出；图不清楚、参数缺失或无法确定时直接指出，不要猜测或补造条件。'''

DIGITAL_PROMPT = '''请解答我随后发送的数字电子技术题目图片，给出正确答案和适合复习的详细解析。

逐题读取逻辑表达式、门电路、真值表、卡诺图及选项，特别核对反相横线覆盖的变量范围、低电平有效标记、与或非及异或运算。先声明变量顺序和采用的规则，再逐步推导，不要跳过关键化简步骤。

逻辑函数化简时说明使用的恒等式；使用卡诺图时说明行列的格雷码顺序、圈组及对应乘积项。最小项、最大项和约束条件必须逐项核对；无关项可取值但不强制覆盖。用真值表、代入或等价变换检查结果，区分最简与或式、最简或与式、反函数和对偶函数。

选择题写明选项及理由，判断题给出依据，填空题按空依次作答，计算题给出完整过程。图像不清、反相横线范围不明或条件不足时明确指出，不要猜测或补造条件。'''

FORMULA_GUIDANCE = (r'公式必须使用 Markdown 中的 LaTeX：行内用 $...$，独立公式用 $$...$$。'
                    r'变量、上下标、分式、反相横线等也写在数学定界符内；例如 '
                    r'$\overline{C}\,\overline{D}$ 表示分别取反，$\overline{CD}$ 表示整体取反。'
                    '不要用 C̄D̄ 这类 Unicode 组合横线或特殊字符拼出公式；'
                    r'使用 \overline、\frac、\oplus 等 LaTeX 命令。不要转义数学定界符，'
                    '不要把整个回复放进代码块。')


def default_templates():
    return [{'id': 'analog', 'name': '模电', 'text': ANALOG_PROMPT},
            {'id': 'digital', 'name': '数电', 'text': DIGITAL_PROMPT}]


def normalize_templates(templates):
    if not isinstance(templates, list) or not templates:
        raise ValueError('至少保留一个提示词模板')
    result, ids = [], set()
    for template in templates:
        if not isinstance(template, dict) or any(not isinstance(template.get(key), str)
                                                for key in ('id', 'name', 'text')):
            raise ValueError('提示词模板格式无效')
        value = {key: template[key].strip() for key in ('id', 'name', 'text')}
        if not all(value.values()):
            raise ValueError('提示词名称和正文不能为空')
        if value['id'] in ids:
            raise ValueError('提示词模板标识重复')
        ids.add(value['id'])
        result.append(value)
    return result


def load_templates(path=DEFAULT_CONFIG):
    path = Path(path)
    if not path.exists():
        return default_templates()
    data = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict) or data.get('version') != 1:
        raise ValueError('提示词配置格式无效')
    return normalize_templates(data.get('templates'))


def save_templates(path, templates):
    templates = normalize_templates(templates)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name('.prompt-config-'+uuid.uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps({'version': 1, 'templates': templates},
                                        ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def add_prompt_arguments(parser):
    parser.add_argument('--prompt-config', type=Path, default=DEFAULT_CONFIG)
    parser.add_argument('--prompt-template', help='提示词模板 ID 或名称；默认使用模电或首个模板')
    parser.add_argument('--prompt-file', type=Path, help='本次提示词正文快照，覆盖所选模板')


def resolve_prompt(args):
    if args.prompt_file is not None:
        text = args.prompt_file.read_text(encoding='utf-8-sig').strip()
        if not text:
            raise ValueError('提示词正文不能为空')
        return text
    templates = load_templates(args.prompt_config)
    if args.prompt_template is None:
        return next((item for item in templates if item['id'] == 'analog'), templates[0])['text']
    template = next((item for item in templates if item['id'] == args.prompt_template
                     or item['name'] == args.prompt_template), None)
    if template is None:
        raise ValueError('所选提示词不存在，请重新选择')
    return template['text']
