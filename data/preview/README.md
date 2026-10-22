# 本地 Markdown 预览资源

Qt WebEngine 加载 `index.html`，使用以下开源项目：

- [markdown-it 14.1.0](https://github.com/markdown-it/markdown-it)：Markdown、表格、代码和上下标 HTML；MIT。
- [markdown-it-texmath 1.0.0](https://github.com/goessner/markdown-it-texmath)：识别 `$…$`、`$$…$$`、`\(…\)`、`\[…\]` 和公式环境；MIT。
- [KaTeX 0.16.22](https://github.com/KaTeX/KaTeX)：公式排版；MIT。
- [DOMPurify 3.2.6](https://github.com/cure53/DOMPurify)：清理生成的 HTML；Apache-2.0 或 MPL-2.0，见随附许可证。

所有 JS、CSS、字体和许可证已随项目保存，运行时不用 CDN，也不用 Node.js。`vendor.json` 记录下载地址、大小和校验摘要。KaTeX CSS 的字体回退项已删去，只引用随附的 WOFF2 字体。`download_assets.py` 是开发时重新下载固定版本的工具，正常使用不会调用。

预览只接受本地静态资源与内嵌题图，禁用外部请求和链接导航。题图从导出目录读取并内嵌，原题填空下划线按普通文字显示，解答按标准 Markdown 显示。
