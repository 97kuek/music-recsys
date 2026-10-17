"""Notebookで明示的に実験JSONを選ぶUI。データ読込み・評価は行わない。"""

import html
from pathlib import Path

from src.configuration import load_settings
from src.experiment_search import load_study_config


class ExperimentConfigPicker:
    def __init__(self, directory: Path):
        import ipywidgets as widgets

        self.directory = Path(directory).resolve()
        self._config = None
        self._content = None
        self.dropdown = widgets.Dropdown(
            options=[("未選択", None)],
            description="実験設定",
            layout=widgets.Layout(width="100%"),
        )
        self.load_button = widgets.Button(description="読み込む", button_style="primary")
        self.refresh_button = widgets.Button(description="一覧を更新")
        self.show_trials = widgets.Checkbox(value=False, description="因子数・近傍数別の設定も表示")
        self.status = widgets.HTML("未選択 / 設定ファイルを選んで読み込んでください")
        self.widget = widgets.VBox(
            [
                self.dropdown,
                self.show_trials,
                widgets.HBox([self.load_button, self.refresh_button]),
                self.status,
            ]
        )
        self.dropdown.observe(self._changed, names="value")
        self.load_button.on_click(self._load)
        self.refresh_button.on_click(self.refresh)
        self.show_trials.observe(self.refresh, names="value")
        self.refresh()

    def _changed(self, change=None):
        self._config = None
        self._content = None
        self.status.value = (
            "未読込み / 読み込むボタンを押してください"
            if self.dropdown.value
            else "未選択 / 設定ファイルを選んでください"
        )

    def refresh(self, button=None):
        options = [("未選択", None)]
        for path in sorted([*self.directory.rglob("*.json"), *self.directory.glob("*.toml")]):
            if not self.show_trials.value and path.parent.name in {"itemcf", "bpr", "als"}:
                if path.parent.name != "als" or path.stem not in {"256", "512"}:
                    continue
            try:
                raw = load_settings(path)
            except (OSError, ValueError):
                # 壊れたJSONも選択でき、読込み時にエラーを明示する
                options.append((path.name + " [形式要確認]", str(path)))
                continue
            if raw.get("experiment", {}).get("protocol") == "listening-v2" and raw.get(
                "model", {}
            ).get("name"):
                options.append((str(path.relative_to(self.directory)), str(path)))
        self.dropdown.options = options
        self.dropdown.value = None
        self._changed()
        if len(options) == 1:
            self.status.value = (
                "設定ファイルなし / configs内にlistening-v2の実験JSONを用意してください"
            )

    def _load(self, button=None):
        self._changed()
        if self.dropdown.value is None:
            return
        path = Path(self.dropdown.value)
        try:
            content = {}
            load_settings(path, sources=content)
            config = load_study_config(path)
            if config.protocol != "listening-v2":
                raise ValueError("listening-v2の実験JSONを選んでください")
            if any(p.read_bytes() != data for p, data in content.items()):
                raise ValueError("設定が変更されました / 再読込みしてください")
        except (OSError, ValueError) as error:
            self.status.value = "読込み失敗 / " + html.escape(str(error))
            return
        self._config, self._content = config, content
        self.status.value = (
            f"読込み済み: {html.escape(path.name)}<br>"
            f"実験: {html.escape(config.name)} / モデル: {html.escape(config.model)}<br>"
            f"入力: {html.escape(str(config.validation_dataset))}<br>"
            "次のセルから順に実行してください / 設定変更後は以降のセルを再実行してください"
        )

    def require_loaded(self):
        if self._config is None:
            raise ValueError(
                "実験設定が未指定または未読込み / 選択欄でファイルを選んで読み込んでください"
            )
        try:
            unchanged = all(p.read_bytes() == data for p, data in self._content.items())
        except OSError:
            unchanged = False
        if not unchanged:
            self._changed()
            raise ValueError("設定ファイルが変更されました / 選択欄から再読込みしてください")
        return self._config

    def require_current(self, config):
        if self.require_loaded() is not config:
            raise ValueError("設定が切り替わっています / 設定確認セルから順に再実行してください")
