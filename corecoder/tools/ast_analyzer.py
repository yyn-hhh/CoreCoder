import ast
import os
from pathlib import Path
from .base import Tool


class ASTAnalyzerTool(Tool):
    name = "ast_analyze"
    description = "分析 Python 文件的导入依赖、函数定义、类定义"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {
                "type": "string",
                "description": "要分析的 Python 文件路径",
            },
            "analysis_type": {
                "type": "string",
                "description": "分析类型: imports(导入), functions(函数), classes(类), all(全部)",
                "default": "all",
            },
        },
        "required": ["file_path"],
    }

    def execute(self, file_path: str, analysis_type: str = "all") -> str:
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                source = f.read()
            tree = ast.parse(source)

            result = {}

            if analysis_type in ("imports", "all"):
                result["imports"] = self._extract_imports(tree)
                result["import_summary"] = self._summarize_imports(result["imports"])

            if analysis_type in ("functions", "all"):
                result["functions"] = self._extract_functions(tree)

            if analysis_type in ("classes", "all"):
                result["classes"] = self._extract_classes(tree)

            return self._format_output(result, file_path)

        except Exception as e:
            return f"AST 分析失败: {e}"

    def _extract_imports(self, tree):
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append({
                        "type": "import",
                        "name": alias.name,
                        "as_name": alias.asname,
                    })
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imports.append({
                        "type": "from_import",
                        "module": module,
                        "name": alias.name,
                        "as_name": alias.asname,
                    })
        return imports

    def _summarize_imports(self, imports):
        """按类型汇总导入"""
        stdlib = []
        third_party = []
        internal = []

        # 简化的判断逻辑
        for imp in imports:
            name = imp.get("module", imp.get("name", ""))
            if name.startswith("."):
                internal.append(name)
            elif name in ("os", "sys", "json", "re", "typing", "pathlib", "datetime",
                          "collections", "itertools", "functools", "subprocess", "ast"):
                stdlib.append(name)
            else:
                third_party.append(name)

        return {
            "stdlib": sorted(set(stdlib)),
            "internal": sorted(set(internal)),
            "third_party": sorted(set(third_party)),
        }

    def _extract_functions(self, tree):
        functions = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                functions.append({
                    "name": node.name,
                    "line": node.lineno,
                    "params": [arg.arg for arg in node.args.args],
                    "decorators": [self._get_decorator_name(d) for d in node.decorator_list],
                })
        return functions

    def _extract_classes(self, tree):
        classes = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                classes.append({
                    "name": node.name,
                    "line": node.lineno,
                    "methods": [m.name for m in node.body if isinstance(m, ast.FunctionDef)],
                    "bases": [self._get_base_name(b) for b in node.bases],
                })
        return classes

    def _get_decorator_name(self, node):
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{node.value.id}.{node.attr}" if hasattr(node.value, 'id') else str(node.attr)
        elif isinstance(node, ast.Call):
            return self._get_decorator_name(node.func) 
        return str(node)

    def _get_base_name(self, node):
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{node.value.id}.{node.attr}" if hasattr(node.value, 'id') else str(node.attr)
        return str(node)

    def _format_output(self, result, file_path):
        output = f"📦 AST 分析: {file_path}\n\n"

        if "imports" in result:
            output += "## 导入依赖\n"
            summary = result.get("import_summary", {})
            output += f"- 标准库: {', '.join(summary.get('stdlib', [])) or '(无)'}\n"
            output += f"- 内部模块: {', '.join(summary.get('internal', [])) or '(无)'}\n"
            output += f"- 第三方库: {', '.join(summary.get('third_party', [])) or '(无)'}\n\n"

        if "functions" in result:
            funcs = result["functions"]
            output += f"## 函数定义 ({len(funcs)} 个)\n"
            for f in funcs[:10]:  # 只显示前10个
                params = ", ".join(f["params"])
                output += f"- `{f['name']}({params})` [Line {f['line']}]\n"
            if len(funcs) > 10:
                output += f"- ... 还有 {len(funcs) - 10} 个函数\n"
            output += "\n"

        if "classes" in result:
            classes = result["classes"]
            output += f"## 类定义 ({len(classes)} 个)\n"
            for c in classes:
                methods = ", ".join(c["methods"][:5])
                if len(c["methods"]) > 5:
                    methods += f"... (+{len(c['methods']) - 5})"
                output += f"- `{c['name']}` [Line {c['line']}]\n"
                output += f"  - 方法: {methods}\n"
                if c["bases"]:
                    output += f"  - 继承: {', '.join(c['bases'])}\n"
            output += "\n"

        return output