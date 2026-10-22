import os
import tempfile
import unittest
from pathlib import Path

from markdown_preview import ASSETS, local_images
from render_homework import BrowserRenderer

ROOT = Path(__file__).resolve().parents[1]


class PreviewTests(unittest.TestCase):
    def test_local_images_embed_and_reject_paths_outside_export(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp:
            directory = Path(temp)
            from PIL import Image
            Image.new('RGB', (20, 30), 'red').save(directory/'image.png')
            self.assertIn('data:image/png;base64,', local_images('![图](image.png)', directory))
            for src in ('../secret.png', 'https://example.test/photo.png', 'file:///C:/secret.png'):
                with self.subTest(src=src), self.assertRaises(ValueError):
                    local_images(f'![图]({src})', directory, strict=True)

    @unittest.skipUnless(os.name == 'nt', 'Local Edge/Chrome')
    def test_display_math_brackets_after_prose(self):
        with BrowserRenderer(960) as renderer:
            renderer.context.unroute('**/*')
            renderer.context.route('**/*', lambda route: route.continue_()
                                   if route.request.url.startswith(('file:', 'data:')) else route.abort())
            renderer.page.goto((ASSETS/'index.html').as_uri())
            answer = (r'**结论**'+'\n\n'+r'官方答案为 **D**，即'+'\n'
                      +r'\['+'\n'+r'\boxed{U_{ab}=-6\,\text{V}}。'+'\n'+r'\]'+'\n\n'
                      +r'取 B 点为参考点，令 $V_B=0$。则'+'\n'
                      +r'\['+'\n'+r'V_P=-6\,\text{V},\qquad V_Q=-12\,\text{V}。'+'\n'+r'\]'+'\n\n'
                      +'```text\n'+r'\['+'\n'+r'x=1'+'\n'+r'\]'+'\n```')
            renderer.page.evaluate('sections => renderSections(sections)', [{'label': '解答', 'text': answer}])
            self.assertEqual(renderer.page.locator('.katex').count(), 3)
            self.assertEqual(renderer.page.locator('.katex-display').count(), 2)
            self.assertEqual(renderer.page.locator('.katex-error').count(), 0)
            self.assertIn(r'\[', renderer.page.locator('pre').inner_text())

    @unittest.skipUnless(os.name == 'nt', 'Local Edge/Chrome')
    def test_offline_markdown_math_images_and_untrusted_markup(self):
        with tempfile.TemporaryDirectory(dir=ROOT) as temp, BrowserRenderer(960) as renderer:
            from PIL import Image
            directory = Path(temp)
            Image.new('RGB', (200, 180), 'red').save(directory/'image.png')
            renderer.context.unroute('**/*')
            renderer.context.route('**/*', lambda route: route.continue_()
                                   if route.request.url.startswith(('file:', 'data:')) else route.abort())
            renderer.page.goto((ASSETS/'index.html').as_uri())
            sections = [{'question': True, 'text': local_images(
                '**原题**：P<sub>CM</sub> 与 x<sup>2</sup>。第一空____；第二空____。\n\n'
                '____\n\n![题图](image.png)', directory)},
                {'label': '解答', 'text': '行内公式 $I=\\frac{U}{R}$。\n\n$$V=2\\,\\mathrm{V}$$\n\n'
                 '|参数|值|\n|---|---|\n|R|1 kΩ|\n\n```text\n$not_math$\n```\n'
                 '<script>window.badExecuted=true</script><img src=x onerror="window.badExecuted=true">'}]
            renderer.page.evaluate('sections => renderSections(sections)', sections)
            renderer.page.evaluate('async () => {await document.fonts.ready; await Promise.all(Array.from(document.images, i=>i.decode()));}')
            self.assertEqual(renderer.page.locator('.katex').count(), 2)
            self.assertEqual(renderer.page.locator('sub').first.inner_text(), 'CM')
            self.assertIn('第一空____；第二空____', renderer.page.locator('main').inner_text())
            self.assertEqual(renderer.page.locator('hr').count(), 0)
            self.assertEqual(renderer.page.locator('table').count(), 1)
            self.assertEqual(renderer.page.locator('pre').inner_text().strip(), '$not_math$')
            self.assertFalse(renderer.page.evaluate('!!window.badExecuted'))
            self.assertTrue(renderer.page.evaluate('Array.from(document.images).every(i=>i.complete && i.naturalWidth>0)'))
            renderer.page.locator('main').screenshot(path=str(directory/'preview.png'))
            with Image.open(directory/'preview.png') as image:
                self.assertEqual(image.width, 960)


if __name__ == '__main__':
    unittest.main()
