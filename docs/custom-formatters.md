<!-- related-cli-options: --custom-formatters, --custom-formatters-kwargs -->

# 🎨 Custom Code Formatters

Create your own custom code formatters for specialized formatting needs.

## 🚀 Usage

Pass the module path containing your formatter class:

```bash
datamodel-codegen --input {your_input_file} --output {your_output_file} --custom-formatters "{path_to_your_module}.your_module"
```

## 📝 Example

### 1️⃣ Create your formatter

### your_module.py

```python
from datamodel_code_generator.format import CustomCodeFormatter


class CodeFormatter(CustomCodeFormatter):
    def apply(self, code: str) -> str:
        # Apply your custom formatting here
        # For example, add a custom header comment:
        header = "# This code was formatted by custom formatter\n"
        return header + code
```

### 2️⃣ Use your formatter

```bash
datamodel-codegen --input schema.json --output model.py --custom-formatters "mypackage.your_module"
```

## 🔧 Passing Arguments

You can pass keyword arguments to your custom formatter using `--custom-formatters-kwargs`:

```bash
datamodel-codegen --input schema.json --output model.py \
    --custom-formatters "mypackage.your_module" \
    --custom-formatters-kwargs '{"line_length": 100}'
```

---

## 📖 See Also

- 🖥️ [CLI Reference: `--custom-formatters`](cli-reference/template-customization.md#custom-formatters) - Detailed CLI option documentation
- 🔧 [CLI Reference: `--custom-formatters-kwargs`](cli-reference/template-customization.md#custom-formatters-kwargs) - Pass arguments to custom formatters
- 🖌️ [Formatting](formatting.md) - Built-in code formatting with black and isort
