## `--generate-prompt` {#generate-prompt}

Generate a prompt for consulting LLMs about CLI options.

Outputs a formatted prompt containing your current options, all available
options by category, and full help text. Pipe to CLI LLM tools or copy
to clipboard for web-based LLM chats.

Use `--output-format json` when an LLM agent or tool should consume structured
option metadata instead of Markdown.
Use `--output-format-json-schema generate-prompt` when the agent needs the JSON
Schema for that structured payload, such as when defining a tool contract.
Add `--recommend-options` (experimental) with an input schema to include the
schema analysis and the options recommended for it in the prompt.

**See also:** [LLM Integration](../../llm-integration.md) for detailed usage examples

!!! note "For LLM agents"

    See [LLM Integration: If You Are an LLM Agent](../../llm-integration.md#if-you-are-an-llm-agent)
    for workflow guidance.

!!! tip "Usage"

    ```bash
    datamodel-codegen --generate-prompt # (1)!
    datamodel-codegen --generate-prompt "How do I generate strict types?" # (2)!
    datamodel-codegen --generate-prompt --output-format json # (3)!
    datamodel-codegen --output-format-json-schema generate-prompt # (4)!
    datamodel-codegen --input schema.json --generate-prompt --recommend-options # (5)!
    ```

    1. :material-arrow-left: `--generate-prompt` - generate prompt without a question
    2. :material-arrow-left: Include a specific question in the prompt
    3. :material-arrow-left: Emit structured JSON for LLM/tool ingestion
    4. :material-arrow-left: Emit JSON Schema for structured prompt JSON
    5. :material-arrow-left: Include the schema analysis and recommended options (experimental)

??? example "Quick Examples"

    **Pipe to CLI tools:**
    ```bash
    datamodel-codegen --generate-prompt | claude -p    # Claude Code
    datamodel-codegen --generate-prompt | codex exec   # OpenAI Codex
    datamodel-codegen --generate-prompt --output-format json | codex exec
    ```

    **Copy to clipboard:**
    ```bash
    datamodel-codegen --generate-prompt | pbcopy      # macOS
    datamodel-codegen --generate-prompt | xclip -selection clipboard  # Linux
    datamodel-codegen --generate-prompt | clip.exe    # WSL2
    ```

    **Ask about an existing OpenAPI command:**
    ```bash
    datamodel-codegen \
        --input openapi.yaml \
        --input-file-type openapi \
        --output models.py \
        --output-model-type pydantic_v2.BaseModel \
        --target-python-version 3.12 \
        --generate-prompt "Find the minimal options for strict API response models." \
        | claude -p
    ```

    **Review a command with current options:**
    ```bash
    datamodel-codegen \
        --input schema.json \
        --output models.py \
        --output-model-type pydantic_v2.BaseModel \
        --generate-prompt "Review this command for stable generated output in CI." \
        | claude -p
    ```
