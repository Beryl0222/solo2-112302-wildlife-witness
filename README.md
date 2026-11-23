# 野生动物线索的语义核验与外发保护

这是面向“巡护记述、区段设施与公开简报”业务文本的 Python 项目。当前目录提供合成练习语料和已登记的原始记录，记录用于开发与回归时复核文字内容和来源关系。

## 内容

- data/corpus.json 保存合成文本记录及记录类别。
- data/context.json 说明当前语料范围。
- materials.py 读取项目语料，保留每条记录的原始文字。
- tests/ 检查资料读取入口和语料索引的完整性。

## 运行和检查

项目仅依赖 Python 标准库。运行检查：

    python3 -m unittest discover -s tests -v
