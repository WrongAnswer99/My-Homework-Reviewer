# 界面数据与预览资源

`gui_state.json` 的 `render_settings` 保存所有作业共用的每页题数、最大图片高度、图片宽度和分组方式。`files` 仅保存各文件的资料位置和待整理的输入。旧版设置首次加载时，从上次选中的文件迁移为全局设置；切换文件或文件夹不会重新加载独立的分组设置。收藏及解答保存在各自的导出目录中。

顶层的 `ocr_model` 保存全局 OCR 模型选择，值为 `v4` 或 `v6tiny`；没有旧配置时默认为 `v4`。实时任务进度仅在界面中显示，不保存为缓存或配置。

`api_config.json` 保存全局模型配置，结构为 `{"version": 1, "profiles": [...]}`。每个条目包含展示名称、模型 ID、Base URL、明文 API Key 和请求参数。文件在配置窗口点击“保存”时创建；`.gitignore` 已排除此文件及其原子保存临时文件。`gui_state.json` 的 `selected_api` 只保存所选配置 ID。

`prompt_templates.json` 保存可编辑的作业提示词，结构为 `{"version": 1, "templates": [...]}`，每个条目包含 `id`、`name`、`text`。初始提供“模电”和“数电”。正文只存解题要求，分题标记、当前题号映射及 LaTeX 公式格式要求在使用时自动附加。`gui_state.json` 的 `selected_prompt_template` 保存全局模板选择；API 重试沿用请求发起时的提示词快照。

`preview/` 保存离线 Markdown / LaTeX 预览使用的前端库、字体及许可文件。

`ui/` 保存界面的勾选标记及上下箭头 SVG 图标，供浅色样式使用，避免系统深色主题导致图标难以辨认。

这里不再保存字形映射或 OCR 识别缓存。`font_map.json`、`ocr_cache.json` 已取消，重新转换会重新识别；无需删除任何缓存来测试 OCR。

OCR 模型位于 `ocr/models/onnx/`。实际送入 OCR 的整题图片保存在各导出目录的 `ocr_images/`，用于与识别结果对照，不会复用为识别缓存。
