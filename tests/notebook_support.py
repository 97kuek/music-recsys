"""テスト用TOMLを選択UIのボタン操作で読み込む。"""


def choose_config(notebook, path):
    import nbformat

    index = next(
        i for i, c in enumerate(notebook.cells) if "config-picker" in c.metadata.get("tags", [])
    )
    notebook.cells.insert(
        index + 1,
        nbformat.v4.new_code_cell(
            f'config_picker.dropdown.options = [("未選択", None), ("test", {str(path)!r})]\n'
            f"config_picker.dropdown.value = {str(path)!r}\n"
            "config_picker.load_button.click()\n"
            "assert config_picker.require_loaded().path == Path(" + repr(str(path)) + ")"
        ),
    )
